"""Astroid's selected source and cache lifecycle, independently of Python imports."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import astroid
import pytest
from astroid.exceptions import AstroidImportError
from astroid.manager import AstroidManager

from clauz3.interp.import_bindings import ImportPlan, plan_import
from clauz3.layer_astroid import AstroidLayerError, astroid_layer


@dataclass
class Image:
    roots: tuple[Path, ...]
    import_roots: tuple[Path, ...]
    paths: tuple[Path, ...]
    owned_modules: dict[str, ModuleType] = field(default_factory=dict)
    astroid_state: object | None = None
    sources: dict[str, tuple[Path, bytes]] = field(default_factory=dict)
    plans: dict[str, ImportPlan] = field(default_factory=dict)

    def source_plan(self, name: str) -> ImportPlan:
        return plan_import(name, import_roots=self.paths)

    def remember_source(self, name: str, path: Path) -> None:
        assert self.source_plan(name).components[-1].origin == path
        current = path, path.read_bytes()
        if name in self.sources and self.sources[name] != current:
            raise RuntimeError("selected source changed")
        self.sources[name] = current

    def remember_plan(self, plan: ImportPlan) -> None:
        assert self.plans.get(plan.name, plan) == plan
        self.plans[plan.name] = plan


def _image(root: Path) -> Image:
    root = root.resolve()
    return Image((root,), (root,), (root,))


def _source(root: Path, name: str, source: str) -> Path:
    path = root.joinpath(*name.split(".")).with_suffix(".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path.resolve()


def _value(module: astroid.Module, name: str = "value") -> object:
    value = next(module.locals[name][0].infer())
    assert isinstance(value, astroid.Const)
    return value.value


def test_ast_only_wrong_source_is_rebuilt_then_restored(tmp_path: Path) -> None:
    name = "astroid_layer_wrong_source"
    a = _source(tmp_path / "a", name, "value = True\n")
    b = _source(tmp_path / "b", name, "value = False\n")
    original = astroid.parse(a.read_text(), module_name=name, path=str(a))
    image = _image(b.parent)
    with astroid_layer(image):
        selected = astroid.MANAGER.ast_from_module_name(name)
        assert _value(selected) is False
        assert Path(selected.file).resolve() == b
        assert selected is not original
        assert name not in sys.modules
    assert astroid.MANAGER.astroid_cache[name] is original
    assert image.sources[name] == (b, b.read_bytes())


def test_repeated_images_reuse_their_ast_identities(tmp_path: Path) -> None:
    name = "astroid_layer_switch"
    _source(tmp_path / "a", name, "value = True\n")
    _source(tmp_path / "b", name, "value = False\n")
    a, b = _image(tmp_path / "a"), _image(tmp_path / "b")
    chosen = []
    for image, expected in ((a, True), (b, False), (a, True)):
        with astroid_layer(image):
            # Module.import_module constructs fresh managers: the adapter must
            # cover those managers, not only the global MANAGER instance.
            selected = AstroidManager().ast_from_module_name(name)
            assert _value(selected) is expected
            chosen.append(selected)
    assert chosen[0] is chosen[2]
    assert chosen[0] is not chosen[1]


def test_relative_import_does_not_execute_parent(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    _source(
        tmp_path,
        "astroid_layer_package.__init__",
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
    )
    _source(tmp_path, "astroid_layer_package.helper", "value = 42\n")
    _source(
        tmp_path,
        "astroid_layer_package.client",
        "from .helper import value\nanswer = value\n",
    )
    with astroid_layer(_image(tmp_path)):
        client = astroid.MANAGER.ast_from_module_name("astroid_layer_package.client")
        assert _value(client, "answer") == 42
    assert not marker.exists()
    assert "astroid_layer_package" not in sys.modules


def test_namespace_source_is_explicit_and_remembered(tmp_path: Path) -> None:
    _source(tmp_path, "astroid_layer_namespace.helper", "value = 7\n")
    image = _image(tmp_path)
    with astroid_layer(image):
        namespace = astroid.MANAGER.ast_from_module_name("astroid_layer_namespace")
        assert namespace.package
        assert namespace.path == [str(tmp_path / "astroid_layer_namespace")]
        assert "astroid_layer_namespace" in image.plans
        helper = namespace.import_module("helper", relative_only=True, level=1)
        assert _value(helper) == 7


def test_cache_journal_restores_all_keys_including_fileless(
    tmp_path: Path,
) -> None:
    cache = astroid.MANAGER.astroid_cache
    files: dict[Any, Any] = astroid.MANAGER._mod_file_cache
    a = astroid.parse("", module_name="astroid_layer_existing")
    old_file = object()
    files[("astroid_layer_existing", None)] = old_file
    before, before_files = cache.copy(), files.copy()
    with (
        pytest.raises(RuntimeError, match="body failed"),
        astroid_layer(_image(tmp_path)),
    ):
        cache.pop("astroid_layer_existing")
        cache["astroid_layer_fileless"] = astroid.parse("")
        cache["astroid_layer_fileless"].file = None
        cache["astroid_layer_existing"] = astroid.parse("")
        files[("astroid_layer_fileless", None)] = object()
        raise RuntimeError("body failed")
    assert cache.keys() == before.keys()
    assert all(cache[key] is value for key, value in before.items())
    assert files.keys() == before_files.keys()
    assert all(files[key] is value for key, value in before_files.items())
    assert cache["astroid_layer_existing"] is a
    # This deliberately nonstandard entry belongs only to this test.
    del files[("astroid_layer_existing", None)]


def test_fileless_local_alias_cannot_supply_authority(tmp_path: Path) -> None:
    name = "astroid_layer_fileless_alias"
    _source(tmp_path, name, "value = False\n")
    alias = astroid.parse("value = True\n")
    alias.file = None
    astroid.MANAGER.astroid_cache[name] = alias
    with astroid_layer(_image(tmp_path)):
        assert _value(astroid.MANAGER.ast_from_module_name(name)) is False
    assert astroid.MANAGER.astroid_cache[name] is alias


def test_source_change_refuses_and_restores_adapter(tmp_path: Path) -> None:
    name = "astroid_layer_changed"
    path = _source(tmp_path, name, "value = True\n")
    image = _image(tmp_path)
    resolver = AstroidManager.ast_from_module_name
    with astroid_layer(image):
        assert _value(astroid.MANAGER.ast_from_module_name(name)) is True
    path.write_text("value = False\n")
    with (
        pytest.raises(RuntimeError, match="selected source changed"),
        astroid_layer(image),
    ):
        astroid.MANAGER.ast_from_module_name(name)
    assert AstroidManager.ast_from_module_name is resolver
    with astroid_layer(_image(tmp_path)):
        assert _value(astroid.MANAGER.ast_from_module_name(name)) is False


def test_local_resolution_does_not_call_import_or_failed_import_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = "astroid_layer_no_hooks"
    _source(tmp_path, name, "value = 3\n")

    class Finder:
        @staticmethod
        def find_spec(*args: object, **kwargs: object) -> None:
            raise AssertionError("executed meta path finder")

    def failed_import(name: str) -> astroid.Module:
        raise AssertionError("executed Astroid failed import hook")

    # Initialize cache support modules before forbidding Python's hooks.
    with astroid_layer(_image(tmp_path)):
        pass
    monkeypatch.setattr(sys, "meta_path", [Finder()])
    monkeypatch.setattr(astroid.MANAGER, "_failed_import_hooks", [failed_import])
    with astroid_layer(_image(tmp_path)):
        assert _value(astroid.MANAGER.ast_from_module_name(name)) == 3
        with pytest.raises(AstroidImportError, match="No selected source"):
            astroid.MANAGER.ast_from_module_name("astroid_layer_not_present")


def test_unknown_astroid_layout_refuses_without_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = astroid.MANAGER.astroid_cache.copy()
    resolver = AstroidManager.ast_from_module_name
    transform = astroid.MANAGER._transform
    monkeypatch.setattr(astroid, "__version__", "99.0.0")
    with (
        pytest.raises(AstroidLayerError, match="supported Astroid"),
        astroid_layer(_image(tmp_path)),
    ):
        pytest.fail("entered an unsupported Astroid layout")
    assert astroid.MANAGER._transform is transform
    assert AstroidManager.ast_from_module_name is resolver
    assert astroid.MANAGER.astroid_cache.keys() == cache.keys()
    assert all(astroid.MANAGER.astroid_cache[k] is v for k, v in cache.items())


def test_engine_and_extension_identities_survive(tmp_path: Path) -> None:
    names = ("clauz3", "astroid", "z3", "math")
    modules = {name: importlib.import_module(name) for name in names}
    builtins = astroid.MANAGER.ast_from_module_name("builtins")
    transform = astroid.MANAGER._transform
    with astroid_layer(_image(tmp_path)):
        assert astroid.MANAGER.ast_from_module_name("builtins") is builtins
        math = astroid.MANAGER.ast_from_module_name("math")
        assert "sin" in math.locals
    assert astroid.MANAGER._transform is transform
    assert astroid.MANAGER.astroid_cache["builtins"] is builtins
    assert all(sys.modules[name] is module for name, module in modules.items())


def test_same_image_nesting_and_different_image_refusal(tmp_path: Path) -> None:
    image = _image(tmp_path)
    with astroid_layer(image):
        resolver = AstroidManager.ast_from_module_name
        with astroid_layer(image):
            assert AstroidManager.ast_from_module_name is resolver
        with (
            pytest.raises(AstroidLayerError, match="different Astroid"),
            astroid_layer(_image(tmp_path)),
        ):
            pytest.fail("entered a different image")
        assert AstroidManager.ast_from_module_name is resolver


def test_environment_source_ignores_fileless_and_module_file_poison(
    tmp_path: Path,
) -> None:
    image = _image(tmp_path)
    poison_path = _source(tmp_path, "astroid_layer_wrong_typing", "value = True\n")
    with astroid_layer(image):
        cache = astroid.MANAGER.astroid_cache
        files: dict[Any, Any] = astroid.MANAGER._mod_file_cache
        alias = astroid.parse("value = True\n")
        alias.file = None
        cache["typing"] = alias
        # Populate the root-insensitive file cache after activation too: it
        # must not override a subsequent verified source plan.
        spec = importlib.import_module("astroid.interpreter._import.spec")
        files[("typing", None)] = spec.ModuleSpec(
            name="typing",
            type=spec.ModuleType.PY_SOURCE,
            location=str(poison_path),
            origin=str(poison_path),
            submodule_search_locations=None,
        )
        selected = astroid.MANAGER.ast_from_module_name("typing")
        expected = image.source_plan("typing").components[-1].origin
        assert expected is not None
        assert Path(selected.file).resolve() == expected
        assert selected is not alias
        assert "TypeVar" in selected.locals
        assert "value" not in selected.locals


def test_unknown_manager_cache_layout_refuses_before_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = AstroidManager.ast_from_module_name
    original = astroid.MANAGER.astroid_cache
    monkeypatch.setattr(astroid.MANAGER, "astroid_cache", original.copy())
    with (
        pytest.raises(AstroidLayerError, match="cache layout"),
        astroid_layer(_image(tmp_path)),
    ):
        pytest.fail("entered an incompatible manager layout")
    assert AstroidManager.ast_from_module_name is resolver
    assert AstroidManager.brain["astroid_cache"] is original


def test_frozen_module_does_not_accept_another_environment_file(tmp_path: Path) -> None:
    with astroid_layer(_image(tmp_path)):
        wrong = astroid.parse("value = True\n", path=astroid.__file__)
        astroid.MANAGER.astroid_cache["os"] = wrong
        selected = astroid.MANAGER.ast_from_module_name("os")
        expected = _image(tmp_path).source_plan("os").components[-1].origin
        assert expected is not None
        assert Path(selected.file).resolve() == expected
        assert selected is not wrong
        assert "value" not in selected.locals

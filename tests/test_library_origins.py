"""Library origin planning must not initialize code or consult cached hooks."""

import importlib
import importlib.util
import sys
from collections.abc import Iterable
from importlib.machinery import ModuleSpec, SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import uuid4

import pytest

from clauz3.interp import import_bindings, library_origins
from clauz3.interp.errors import TrustedBoundaryError
from clauz3.interp.library_origins import (
    validate_library_module,
    validate_trusted_module,
)
from clauz3.prover import _load_trusted_modules


def _name() -> str:
    return f"library_origin_{uuid4().hex}"


def _authorize(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_origins, "_AUTHORITY_ROOTS", (path.resolve(),))


def _module(name: str, source: Path, *, package: bool = False) -> ModuleType:
    module = ModuleType(name)
    module.__file__ = str(source)
    loader = SourceFileLoader(name, str(source))
    module.__loader__ = loader
    module.__spec__ = ModuleSpec(name, loader, origin=str(source), is_package=package)
    if package:
        paths = [str(source.parent)]
        module.__path__ = paths
        module.__spec__.submodule_search_locations = paths
    return module


@pytest.mark.parametrize(
    "name", ["sys", "math", "os.path", "collections.abc", "typing", "clauz3.spec"]
)
def test_standard_and_framework_origins_need_no_import_hooks(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("origin validation must not invoke import hooks")

    class Finder:
        find_spec = staticmethod(forbidden)

    monkeypatch.setattr(importlib, "import_module", forbidden)
    monkeypatch.setattr(importlib.util, "find_spec", forbidden)
    monkeypatch.setattr(sys, "meta_path", [Finder()])
    monkeypatch.setattr(sys, "path_hooks", [forbidden])

    plan = validate_library_module(name)

    assert plan.name == name
    assert plan.components[-1].name == name


@pytest.mark.parametrize("configured", [True, False], ids=["source-root", "ambient"])
def test_agent_and_ambient_shadow_origins_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: bool
) -> None:
    source = tmp_path / "statistics.py"
    source.write_text("raise AssertionError('initializer must not run')\n")
    roots = [tmp_path] if configured else []
    if not configured:
        monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(
        TrustedBoundaryError, match="outside authorized library origins"
    ):
        validate_library_module("statistics", import_roots=roots)


def test_untrusted_parent_cannot_borrow_an_authorized_child_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorized = tmp_path / "installed"
    authorized.mkdir()
    _authorize(authorized, monkeypatch)
    child = authorized / "child.py"
    child.write_text("")
    name = _name()
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text("raise AssertionError('parent ran')\n")
    (package / "child.py").symlink_to(child)

    with pytest.raises(
        TrustedBoundaryError, match="outside authorized library origins"
    ):
        validate_library_module(f"{name}.child", import_roots=[tmp_path])

    assert name not in sys.modules


@pytest.mark.parametrize("outward", [True, False], ids=["escape", "safe-alias"])
def test_authority_uses_resolved_symlink_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outward: bool
) -> None:
    authorized, outside = tmp_path / "installed", tmp_path / "agent"
    authorized.mkdir()
    outside.mkdir()
    _authorize(authorized, monkeypatch)
    name = _name()
    destination = (outside if outward else authorized) / f"{name}.py"
    destination.write_text("raise AssertionError('must not initialize')\n")
    search = authorized if outward else outside
    (search / f"{name}.py").symlink_to(destination)

    if outward:
        with pytest.raises(TrustedBoundaryError, match="outside authorized"):
            validate_library_module(name, import_roots=[search])
    else:
        plan = validate_library_module(name, import_roots=[search])
        assert plan.components[-1].origin == destination.resolve()


def test_mixed_namespace_portions_are_not_authorized_by_a_safe_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorized, outside = tmp_path / "installed", tmp_path / "agent"
    name = _name()
    (authorized / name).mkdir(parents=True)
    (outside / name).mkdir(parents=True)
    (authorized / name / "child.py").write_text("")
    _authorize(authorized, monkeypatch)

    with pytest.raises(TrustedBoundaryError, match="outside authorized"):
        validate_library_module(f"{name}.child", import_roots=[outside, authorized])


def test_regular_package_wins_over_an_earlier_namespace_portion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorized, outside = tmp_path / "installed", tmp_path / "agent"
    name = _name()
    (authorized / name).mkdir(parents=True)
    (outside / name).mkdir(parents=True)
    (authorized / name / "__init__.py").write_text("")
    (authorized / name / "child.py").write_text("")
    _authorize(authorized, monkeypatch)

    plan = validate_library_module(f"{name}.child", import_roots=[outside, authorized])

    assert plan.components[0].locations == ((authorized / name).resolve(),)


def test_regular_package_does_not_search_a_later_namespace_for_missing_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorized, outside = tmp_path / "installed", tmp_path / "agent"
    name = _name()
    (authorized / name).mkdir(parents=True)
    (outside / name).mkdir(parents=True)
    (authorized / name / "__init__.py").write_text("")
    (outside / name / "child.py").write_text("")
    _authorize(authorized, monkeypatch)

    with pytest.raises(ModuleNotFoundError):
        validate_library_module(f"{name}.child", import_roots=[authorized, outside])


def test_builtin_priority_is_preserved_over_a_filesystem_candidate(
    tmp_path: Path,
) -> None:
    (tmp_path / "sys.py").write_text("raise AssertionError('must not initialize')\n")

    plan = validate_library_module("sys", import_roots=[tmp_path])

    assert plan.components[-1].kind == "builtin"


@pytest.mark.parametrize("wrong_file", [True, False], ids=["mismatch", "matching"])
def test_cached_files_must_match_the_selected_source_without_getattr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrong_file: bool
) -> None:
    _authorize(tmp_path, monkeypatch)
    name = _name()
    source = tmp_path / f"{name}.py"
    source.write_text("")
    cached = _module(name, tmp_path / "different.py" if wrong_file else source)

    def forbidden(attribute: str) -> object:
        raise AssertionError(f"must not look up cached attribute {attribute}")

    cached.__dict__["__getattr__"] = forbidden
    monkeypatch.setitem(sys.modules, name, cached)
    if wrong_file:
        with pytest.raises(TrustedBoundaryError, match="cached module.*does not match"):
            validate_library_module(name, import_roots=[tmp_path])
    else:
        validate_library_module(name, import_roots=[tmp_path])
    assert sys.modules[name] is cached


def test_cached_module_subclass_cannot_execute_attribute_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _authorize(tmp_path, monkeypatch)
    name = _name()
    (tmp_path / f"{name}.py").write_text("")
    reads: list[str] = []

    class Poison(ModuleType):
        def __getattribute__(self, attribute: str) -> object:
            reads.append(attribute)
            raise AssertionError("must not access a cached module subclass")

    monkeypatch.setitem(sys.modules, name, Poison(name))

    with pytest.raises(TrustedBoundaryError, match="not a standard module"):
        validate_library_module(name, import_roots=[tmp_path])

    assert reads == []


def test_nonmodule_cache_object_cannot_execute_class_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _authorize(tmp_path, monkeypatch)
    name = _name()
    (tmp_path / f"{name}.py").write_text("")
    reads: list[str] = []

    class Poison:
        def __getattribute__(self, attribute: str) -> object:
            reads.append(attribute)
            raise AssertionError("must not inspect an arbitrary cached object")

    monkeypatch.setitem(sys.modules, name, cast(ModuleType, Poison()))

    with pytest.raises(TrustedBoundaryError, match="not a standard module"):
        validate_library_module(name, import_roots=[tmp_path])

    assert reads == []


@pytest.mark.parametrize("hostile", [True, False], ids=["hook", "outside-path"])
def test_cached_parent_paths_are_checked_without_iterating_arbitrary_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hostile: bool
) -> None:
    _authorize(tmp_path, monkeypatch)
    name = _name()
    package = tmp_path / name
    package.mkdir()
    source = package / "__init__.py"
    source.write_text("")
    (package / "child.py").write_text("")
    cached = _module(name, source, package=True)

    class Poison:
        def __iter__(self) -> Iterable[str]:
            raise AssertionError("must not iterate an arbitrary cached path")

    cached.__dict__["__path__"] = Poison() if hostile else [str(tmp_path)]
    monkeypatch.setitem(sys.modules, name, cached)

    with pytest.raises(TrustedBoundaryError, match="package search path"):
        validate_library_module(f"{name}.child", import_roots=[tmp_path])


def test_genuine_cached_namespace_path_uses_its_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _authorize(tmp_path, monkeypatch)
    name = _name()
    package = tmp_path / name
    package.mkdir()
    (package / "child.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    namespace = importlib.import_module(name)
    del sys.modules[name]
    monkeypatch.setitem(sys.modules, name, namespace)

    plan = validate_library_module(f"{name}.child", import_roots=[tmp_path])

    assert plan.components[0].kind == "namespace"
    assert plan.components[0].locations == (package.resolve(),)


@pytest.mark.parametrize(
    "cache_name",
    ["os", "os.path", "ntpath" if sys.platform == "win32" else "posixpath"],
)
def test_os_path_alias_checks_parent_canonical_and_alias_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_name: str
) -> None:
    monkeypatch.setitem(
        sys.modules, cache_name, _module(cache_name, tmp_path / "fake.py")
    )

    with pytest.raises(TrustedBoundaryError, match="cached module.*does not match"):
        validate_library_module("os.path")


def test_collections_alias_fallback_retains_the_parent_and_canonical_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = import_bindings._find_spec

    def absent_alias(name: str, paths: Iterable[str]) -> ModuleSpec | None:
        return None if name == "collections.abc" else original(name, paths)

    monkeypatch.setattr(import_bindings, "_find_spec", absent_alias)
    monkeypatch.delitem(sys.modules, "collections.abc", raising=False)

    plan = validate_library_module("collections.abc")

    assert tuple(item.name for item in plan.components) == (
        "collections",
        "_collections_abc",
        "collections.abc",
    )
    assert plan.components[-1].resolved_name == "_collections_abc"


@pytest.mark.parametrize("initializer", ["", '"Package documentation."\npass\n'])
def test_even_inert_parent_source_requires_explicit_trust(
    tmp_path: Path, initializer: str
) -> None:
    name = _name()
    parent = tmp_path / name
    trusted = parent / "trusted"
    trusted.mkdir(parents=True)
    (parent / "__init__.py").write_text(initializer)
    source = trusted / "effects.py"
    source.write_text("")

    with pytest.raises(TrustedBoundaryError, match="parent module.*outside"):
        validate_trusted_module(
            f"{name}.trusted.effects",
            source=source,
            trusted_roots=[trusted],
            import_roots=[tmp_path],
        )

    assert name not in sys.modules


def test_explicitly_trusted_parent_initializer_is_planned_without_execution(
    tmp_path: Path,
) -> None:
    name = _name()
    parent = tmp_path / name
    trusted = parent / "trusted"
    trusted.mkdir(parents=True)
    (parent / "__init__.py").write_text("raise AssertionError('do not import')\n")
    source = trusted / "effects.py"
    source.write_text("")

    plan = validate_trusted_module(
        f"{name}.trusted.effects",
        source=source,
        trusted_roots=[parent],
        import_roots=[tmp_path],
    )

    assert plan.components[-1].origin == source.resolve()
    assert name not in sys.modules


def test_enumerated_symlink_target_still_requires_trusted_root_authority(
    tmp_path: Path,
) -> None:
    name = _name()
    trusted = tmp_path / name
    trusted.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("raise AssertionError('do not import')\n")
    source = trusted / "effects.py"
    source.symlink_to(outside)

    with pytest.raises(TrustedBoundaryError, match="source.*outside"):
        validate_trusted_module(
            f"{name}.effects",
            source=source,
            trusted_roots=[trusted],
            import_roots=[tmp_path],
        )

    assert name not in sys.modules


def test_entire_trusted_inventory_is_checked_before_the_first_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted = tmp_path / "trusted"
    agent = tmp_path / "agent"
    trusted.mkdir()
    agent.mkdir()
    (trusted / "a_valid.py").write_text("")
    (trusted / "z_shadowed.py").write_text("")
    (agent / "z_shadowed.py").write_text("")

    def forbidden_import(name: str) -> ModuleType:
        raise AssertionError(f"import started before complete validation: {name}")

    monkeypatch.setattr(importlib, "import_module", forbidden_import)
    with pytest.raises(TrustedBoundaryError, match="selected source.*not its"):
        _load_trusted_modules([trusted], import_paths=[agent, trusted])

    assert "a_valid" not in sys.modules

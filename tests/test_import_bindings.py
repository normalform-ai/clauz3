"""The module gate can check import paths without running their code."""

import importlib
import sys
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

from clauz3.interp.errors import TrustedBoundaryError
from clauz3.interp.import_bindings import loaded_trusted_module, require_importable


def _name() -> str:
    return f"static_import_{uuid4().hex}"


@pytest.mark.parametrize("name", ["sys", "os", "os.path", "math", "collections.abc"])
def test_standard_modules_can_be_found_without_import_module(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    def forbidden_import(*args: object, **kwargs: object) -> None:
        raise AssertionError("validation must not import a module")

    monkeypatch.setattr(importlib, "import_module", forbidden_import)

    require_importable(name, [])


def test_package_and_child_initializers_are_not_executed(tmp_path: Path) -> None:
    name = _name()
    package = tmp_path / name
    package.mkdir()
    marker = tmp_path / "executed"
    payload = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    (package / "__init__.py").write_text(payload)
    (package / "child.py").write_text(payload)

    require_importable(f"{name}.child", [tmp_path])

    assert not marker.exists()
    assert name not in sys.modules
    assert f"{name}.child" not in sys.modules


def test_dotted_missing_module_is_not_imported_to_check_existence(
    tmp_path: Path,
) -> None:
    name = _name()
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text("raise AssertionError('must not run')\n")
    missing = f"{name}.missing"

    with pytest.raises(ModuleNotFoundError, match=f"No module named '{missing}'"):
        require_importable(missing, [tmp_path])

    assert name not in sys.modules


def test_missing_top_level_module_has_the_requested_name(tmp_path: Path) -> None:
    name = _name()

    with pytest.raises(ModuleNotFoundError) as exc_info:
        require_importable(name, [tmp_path])

    assert str(exc_info.value) == f"No module named '{name}'"
    assert exc_info.value.name == name


def test_namespace_portions_are_combined_without_importing_parents(
    tmp_path: Path,
) -> None:
    name = _name()
    left, right = tmp_path / "left", tmp_path / "right"
    (left / name / "nested").mkdir(parents=True)
    (right / name / "nested").mkdir(parents=True)
    (right / name / "nested" / "child.py").write_text("raise AssertionError\n")

    require_importable(f"{name}.nested.child", [left, right])

    assert name not in sys.modules
    assert f"{name}.nested" not in sys.modules


def test_regular_package_overrides_earlier_namespace_portion(tmp_path: Path) -> None:
    name = _name()
    left, right = tmp_path / "left", tmp_path / "right"
    (left / name).mkdir(parents=True)
    (left / name / "child.py").write_text("")
    (right / name).mkdir(parents=True)
    (right / name / "__init__.py").write_text("")

    with pytest.raises(ModuleNotFoundError):
        require_importable(f"{name}.child", [left, right])


def test_regular_package_prevents_later_namespace_fallback(tmp_path: Path) -> None:
    name = _name()
    left, right = tmp_path / "left", tmp_path / "right"
    (left / name).mkdir(parents=True)
    (left / name / "__init__.py").write_text("")
    (right / name).mkdir(parents=True)
    (right / name / "child.py").write_text("")

    with pytest.raises(ModuleNotFoundError):
        require_importable(f"{name}.child", [left, right])


def test_regular_module_prevents_submodule_lookup(tmp_path: Path) -> None:
    name = _name()
    (tmp_path / f"{name}.py").write_text("")

    with pytest.raises(ModuleNotFoundError, match="is not a package"):
        require_importable(f"{name}.child", [tmp_path])


def test_namespace_lookup_does_not_read_poisoned_cached_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _name()
    (tmp_path / name / "nested").mkdir(parents=True)
    (tmp_path / name / "nested" / "child.py").write_text("")
    poison = ModuleType(name)
    reads: list[str] = []

    def forbidden_attribute(attribute: str) -> object:
        reads.append(attribute)
        raise AssertionError("must not inspect a cached parent")

    poison.__dict__["__getattr__"] = forbidden_attribute
    monkeypatch.setitem(sys.modules, name, poison)
    monkeypatch.setitem(sys.modules, f"{name}.nested", poison)

    require_importable(f"{name}.nested.child", [tmp_path])

    assert reads == []
    assert sys.modules[name] is poison


def test_cached_module_does_not_establish_filesystem_existence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _name()
    monkeypatch.setitem(sys.modules, name, ModuleType(name))

    with pytest.raises(ModuleNotFoundError):
        require_importable(name, [tmp_path])


def test_empty_sys_path_entry_means_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _name()
    (tmp_path / f"{name}.py").write_text("raise AssertionError\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [""])

    require_importable(name, [])

    assert name not in sys.modules


def test_trusted_file_must_be_preloaded_without_running_its_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _name()
    trusted = tmp_path / name
    trusted.mkdir()
    source = trusted / "effects.py"
    marker = tmp_path / "executed"
    source.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    module_name = f"{name}.effects"

    with pytest.raises(TrustedBoundaryError, match="not loaded from"):
        loaded_trusted_module(
            module_name, trusted_roots=[trusted], import_roots=[tmp_path]
        )

    assert not marker.exists()
    assert module_name not in sys.modules
    cached = ModuleType(module_name)
    cached.__file__ = str(source)
    monkeypatch.setitem(sys.modules, module_name, cached)
    assert (
        loaded_trusted_module(
            module_name, trusted_roots=[trusted], import_roots=[tmp_path]
        )
        is cached
    )
    assert not marker.exists()


def test_trusted_cached_file_must_match_the_requested_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _name()
    trusted = tmp_path / name
    trusted.mkdir()
    (trusted / "effects.py").write_text("")
    other = trusted / "other.py"
    other.write_text("")
    module_name = f"{name}.effects"
    cached = ModuleType(module_name)
    cached.__file__ = str(other)
    monkeypatch.setitem(sys.modules, module_name, cached)

    with pytest.raises(TrustedBoundaryError, match="not loaded from"):
        loaded_trusted_module(
            module_name, trusted_roots=[trusted], import_roots=[tmp_path]
        )


def test_trusted_file_symlink_cannot_escape_the_configured_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _name()
    trusted = tmp_path / name
    trusted.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("")
    source = trusted / "effects.py"
    source.symlink_to(outside)
    module_name = f"{name}.effects"
    cached = ModuleType(module_name)
    cached.__file__ = str(source)
    monkeypatch.setitem(sys.modules, module_name, cached)

    with pytest.raises(TrustedBoundaryError, match="not loaded from"):
        loaded_trusted_module(
            module_name, trusted_roots=[trusted], import_roots=[tmp_path]
        )


def test_trusted_namespace_binding_does_not_read_cached_attributes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = _name()
    trusted = tmp_path / name
    trusted.mkdir()
    cached = ModuleType(name)

    def forbidden_attribute(attribute: str) -> object:
        raise AssertionError(f"must not inspect cached {attribute}")

    cached.__dict__["__getattr__"] = forbidden_attribute
    monkeypatch.setitem(sys.modules, name, cached)

    assert (
        loaded_trusted_module(name, trusted_roots=[trusted], import_roots=[tmp_path])
        is None
    )

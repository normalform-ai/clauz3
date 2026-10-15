"""Pure import batches reuse finders without retaining stale directory state."""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from importlib.machinery import FileFinder, ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest

from clauz3.interp.errors import TrustedBoundaryError
from clauz3.interp.import_bindings import (
    ImportComponent,
    plan_import,
    resolution_phase,
    resolve_path,
)
from clauz3.interp.library_origins import _check_cached
from clauz3.layer_state import _origin


def test_resolution_phase_reuses_findings_without_import_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = f"phase_a_{uuid4().hex}", f"phase_b_{uuid4().hex}"
    for name in (first, second):
        (tmp_path / f"{name}.py").write_text(
            "raise AssertionError('initializer ran')\n"
        )
    created: list[Path] = []

    def new_finder(path: str, *loaders: Any) -> FileFinder:
        created.append(Path(path).resolve())
        return FileFinder(path, *loaders)

    def forbidden_hook(*args: object, **kwargs: object) -> None:
        raise AssertionError("custom import hook ran")

    class Finder:
        find_spec = staticmethod(forbidden_hook)

    monkeypatch.setattr("clauz3.interp.import_bindings.FileFinder", new_finder)
    monkeypatch.setattr(sys, "path_hooks", [forbidden_hook])
    monkeypatch.setattr(sys, "meta_path", [Finder()])
    with resolution_phase():
        assert plan_import(first, import_roots=[tmp_path]).name == first
        with resolution_phase():
            assert plan_import(second, import_roots=[tmp_path]).name == second
        assert created.count(tmp_path.resolve()) == 1
    with resolution_phase():
        assert plan_import(first, import_roots=[tmp_path]).name == first
    assert created.count(tmp_path.resolve()) == 2
    assert first not in sys.modules
    assert second not in sys.modules


@pytest.mark.parametrize("failed_phase", [False, True])
def test_new_file_is_seen_between_phases_even_with_restored_directory_time(
    tmp_path: Path, failed_phase: bool
) -> None:
    name = f"new_phase_{uuid4().hex}"
    directory = tmp_path.stat()
    try:
        with resolution_phase():
            with pytest.raises(ModuleNotFoundError):
                plan_import(name, import_roots=[tmp_path])
            if failed_phase:
                raise ValueError("end failed phase")
    except ValueError:
        assert failed_phase
    source = tmp_path / f"{name}.py"
    source.write_text("raise AssertionError('initializer ran')\n")
    os.utime(tmp_path, ns=(directory.st_atime_ns, directory.st_mtime_ns))
    with resolution_phase():
        plan = plan_import(name, import_roots=[tmp_path])
    assert plan.components[-1].origin == source.resolve()
    assert name not in sys.modules


@pytest.mark.parametrize("failed_phase", [False, True])
def test_symlink_retarget_is_seen_after_phase_with_restored_directory_time(
    tmp_path: Path, failed_phase: bool
) -> None:
    name = f"retarget_{uuid4().hex}"
    first, second = tmp_path / "first", tmp_path / "second"
    for root in (first, second):
        root.mkdir()
        (root / f"{name}.py").write_text("raise AssertionError('initializer ran')\n")
    link = tmp_path / "selected"
    link.symlink_to(first, target_is_directory=True)
    directory = tmp_path.stat()
    try:
        with resolution_phase():
            assert (
                plan_import(name, import_roots=[link]).components[-1].origin
                == (first / f"{name}.py").resolve()
            )
            if failed_phase:
                raise ValueError("end failed phase")
    except ValueError:
        assert failed_phase
    link.unlink()
    link.symlink_to(second, target_is_directory=True)
    os.utime(tmp_path, ns=(directory.st_atime_ns, directory.st_mtime_ns))
    with resolution_phase():
        assert (
            plan_import(name, import_roots=[link]).components[-1].origin
            == (second / f"{name}.py").resolve()
        )
    assert name not in sys.modules


def test_relative_paths_follow_current_directory_within_and_between_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    with resolution_phase():
        assert resolve_path("relative") == first / "relative"
        monkeypatch.chdir(second)
        assert resolve_path("relative") == second / "relative"
    monkeypatch.chdir(first)
    with resolution_phase():
        assert resolve_path("relative") == first / "relative"


@pytest.mark.parametrize("field", ["module_file", "spec_file", "namespace_paths"])
def test_changed_cached_metadata_is_reread_inside_resolution_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    name = f"metadata_{uuid4().hex}"
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    module = ModuleType(name)
    spec = ModuleSpec(name, loader=None, is_package=True)
    module.__dict__.update(__spec__=spec, __path__=[str(first)])
    spec.submodule_search_locations = [str(first)]
    if field == "namespace_paths":
        component = ImportComponent(name, name, "namespace", None, (first,))
    else:
        original = first / "__init__.py"
        module.__file__ = str(original)
        spec.origin = str(original)
        component = ImportComponent(name, name, "file", original, (first,))

    def forbidden_hook(attribute: str) -> object:
        raise AssertionError(f"cached module hook ran: {attribute}")

    module.__dict__["__getattr__"] = forbidden_hook
    monkeypatch.setitem(sys.modules, name, module)
    with resolution_phase():
        _check_cached(component, requested=name)
        if field == "module_file":
            assert _origin(module) == first / "__init__.py"
            module.__file__ = str(second / "__init__.py")
            assert _origin(module) == second / "__init__.py"
            diagnostic = "source file"
        elif field == "spec_file":
            spec.origin = str(second / "__init__.py")
            diagnostic = "spec source file"
        else:
            module.__dict__["__path__"].append(str(second))
            spec.submodule_search_locations = [str(first), str(second)]
            diagnostic = "package search path"
        with pytest.raises(TrustedBoundaryError, match=diagnostic):
            _check_cached(component, requested=name)


def test_copied_context_cannot_reuse_another_threads_resolved_path(
    tmp_path: Path,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    link = tmp_path / "selected"
    link.symlink_to(first, target_is_directory=True)
    with resolution_phase():
        assert resolve_path(link) == first
        copied = copy_context()
        link.unlink()
        link.symlink_to(second, target_is_directory=True)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(copied.run, resolve_path, link).result() == second

"""Select Astroid source objects inside the coordinator's locked layer window.

Astroid managers share their caches. The coordinator must enter this context
after selecting Python modules and sys.path, and leave it before restoring them.
This adapter neither imports a selected local module nor rebuilds Astroid brains.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import astroid
from astroid import manager as manager_module
from astroid.builder import AstroidBuilder
from astroid.exceptions import AstroidImportError
from astroid.manager import AstroidManager

from clauz3.interp.import_bindings import ImportComponent, ImportPlan
from clauz3.interp.library_origins import _AUTHORITY_ROOTS


class AstroidLayerError(RuntimeError):
    """The installed Astroid layout or selected source cannot be verified."""


class _Image(Protocol):
    roots: tuple[Path, ...]
    import_roots: tuple[Path, ...]
    paths: tuple[Path, ...]
    owned_modules: dict[str, ModuleType]
    astroid_state: object | None

    def source_plan(self, name: str) -> ImportPlan: ...

    def remember_source(self, name: str, path: Path) -> None: ...

    def remember_plan(self, plan: ImportPlan) -> None: ...


@dataclass
class _State:
    modules: dict[str, astroid.Module] = field(default_factory=dict)


_ACTIVE: _Image | None = None


def _cache_clearers() -> tuple[Callable[[], object], ...]:
    """Validate supported private cache APIs before making any state changes."""
    if not astroid.__version__.startswith("4.1."):
        raise AstroidLayerError(
            "trusted layer isolation requires the supported Astroid 4.1 cache layout"
        )
    try:
        spec = import_module("astroid.interpreter._import.spec")
        inference_tip = import_module("astroid.inference_tip")
        objectmodel = import_module("astroid.interpreter.objectmodel")
        nodes = import_module("astroid.nodes._base_nodes")
        scoped = import_module("astroid.nodes.scoped_nodes")
        context = import_module("astroid.context")
        cached = [
            manager_module._cache_normalize_path_,
            manager_module._has_init,
            manager_module.cached_os_path_isfile,
            manager_module.util.is_namespace,
            spec._find_spec,
            spec._is_setuptools_namespace,
            nodes.LookupMixIn.lookup,
            objectmodel.ObjectModel.attributes,
            scoped.ClassDef._metaclass_lookup_attribute,
            *(finder.find_module for finder in spec._SPEC_FINDERS),
        ]
        clearers = (
            context._invalidate_cache,
            inference_tip.clear_inference_tip_cache,
            *(item.cache_clear for item in cached),
        )
        if not all(callable(clear) for clear in clearers):
            raise TypeError("unrecognized cache clearer")
        if (
            type(astroid.MANAGER.astroid_cache) is not dict
            or type(astroid.MANAGER._mod_file_cache) is not dict
            or astroid.MANAGER.astroid_cache
            is not AstroidManager.brain["astroid_cache"]
            or astroid.MANAGER._mod_file_cache
            is not AstroidManager.brain["_mod_file_cache"]
        ):
            raise TypeError("unrecognized manager caches")
    except (AttributeError, ImportError, KeyError, TypeError) as exc:
        raise AstroidLayerError(
            "trusted layer isolation cannot use this Astroid cache layout"
        ) from exc
    return cast(tuple[Callable[[], object], ...], clearers)


def _local(component: ImportComponent, image: _Image) -> bool:
    paths = (() if component.origin is None else (component.origin,)) + (
        component.locations or ()
    )
    return any(
        any(path.is_relative_to(root) for root in image.roots)
        or not any(path.is_relative_to(root) for root in _AUTHORITY_ROOTS)
        for path in paths
    )


def _module_file(module: object) -> Path | None:
    if type(module) is not astroid.Module:
        return None
    filename = vars(module).get("file")
    return Path(filename).resolve() if type(filename) is str else None


@contextmanager
def astroid_layer(image: _Image) -> Iterator[None]:
    """Activate verified local ASTs and restore displaced cache entries by identity.

    Named local ASTs persist in the image for resumption. Entry ASTs and arbitrary
    added/rebound cache keys are removed on exit, including file-less entries.
    Existing ambient entries are restored without depending on their provenance.
    """
    global _ACTIVE
    if _ACTIVE is image:
        yield
        return
    if _ACTIVE is not None:
        raise AstroidLayerError("a different Astroid trusted layer is already active")
    clearers = _cache_clearers()
    # Bootstrap once before taking the ambient snapshot. Never clear/rebootstrap
    # the builtins AST: existing Const/type proxies can retain references to it.
    AstroidBuilder(astroid.MANAGER)
    ast_cache = astroid.MANAGER.astroid_cache
    file_cache = astroid.MANAGER._mod_file_cache
    saved_ast = ast_cache.copy()
    saved_files = file_cache.copy()
    previous = AstroidManager.ast_from_module_name
    if image.astroid_state is None:
        image.astroid_state = _State()
    if not isinstance(image.astroid_state, _State):
        raise AstroidLayerError("unrecognized saved Astroid trusted-layer state")
    state = image.astroid_state
    building: set[str] = set()
    environment: dict[str, astroid.Module] = {}

    def resolve(
        manager: AstroidManager,
        modname: str | None,
        context_file: str | None = None,
        use_cache: bool = True,
    ) -> astroid.Module:
        if modname is None:
            raise AstroidImportError("No module name given")
        if modname in manager.module_denylist:
            raise AstroidImportError(f"Skipping ignored module {modname!r}")
        if modname == "__main__":
            # Astroid's explicit entry stub is not imported trusted authority.
            return manager._build_stub_module(modname)
        try:
            plan = image.source_plan(modname)
        except ModuleNotFoundError as exc:
            # Do not invoke Astroid's failed-import hooks for absent local names.
            raise AstroidImportError(
                f"No selected source for module {modname!r}"
            ) from exc
        component = plan.components[-1]
        if not _local(component, image):
            cached = manager.astroid_cache.get(modname)
            path = _module_file(cached)
            verified = cached is not None and (
                cached is environment.get(modname)
                or (modname == "builtins" and cached is saved_ast.get("builtins"))
                or (path is not None and path == component.origin)
            )
            if not verified:
                manager.astroid_cache.pop(modname, None)
            if verified and use_cache:
                return cast(astroid.Module, cached)
            # A fresh file-cache entry must not undo the verified source plan.
            # Parse ordinary environment source directly too; retain Astroid's
            # normal builder and transforms, without repeating name discovery.
            file_cache.pop((modname, context_file), None)
            if component.origin is not None and component.origin.suffix == ".py":
                result = AstroidBuilder(manager).file_build(
                    str(component.origin), modname
                )
                if _module_file(result) != component.origin:
                    raise AstroidLayerError(
                        f"environment AST {modname!r} has an unexpected source"
                    )
            elif component.kind == "namespace":
                result = manager._build_namespace_module(
                    modname, [str(path) for path in component.locations or ()]
                )
            else:
                result = previous(manager, modname, context_file, use_cache)
                result_path = _module_file(result)
                # Builtin models and intentionally empty extension/frozen
                # stubs have explicit engine provenance, not a Python file.
                engine_ast = (
                    result_path is None
                    and result.name == modname
                    and (
                        component.kind in {"builtin", "frozen"}
                        or (not result.body and not result.locals)
                    )
                )
                if not engine_ast and result_path != component.origin:
                    raise AstroidLayerError(
                        f"environment AST {modname!r} has an unexpected source"
                    )
            environment[modname] = result
            return result

        image.remember_plan(plan)
        if component.kind == "namespace":
            module = state.modules.get(modname)
            if module is None or not use_cache:
                module = manager._build_namespace_module(
                    modname, [str(path) for path in component.locations or ()]
                )
                state.modules[modname] = module
            manager.astroid_cache[modname] = module
            return module
        path = component.origin
        if component.kind != "file" or path is None or path.suffix != ".py":
            raise AstroidLayerError(
                f"local module {modname!r} has no supported Python source file"
            )
        image.remember_source(modname, path)
        if modname in building:
            module = manager.astroid_cache.get(modname)
            if type(module) is astroid.Module and _module_file(module) == path:
                return module
            raise AstroidLayerError(f"unresolved circular source import {modname!r}")
        module = state.modules.get(modname) if use_cache else None
        if module is None:
            # Ignore an ambient AST even when its filename matches: the image
            # has not yet established when that AST's source was read.
            manager.astroid_cache.pop(modname, None)
            building.add(modname)
            try:
                module = AstroidBuilder(manager).file_build(str(path), modname)
            finally:
                building.remove(modname)
        if _module_file(module) != path:
            raise AstroidLayerError(
                f"Astroid module {modname!r} did not resolve to selected source {path}"
            )
        state.modules[modname] = module
        manager.astroid_cache[modname] = module
        return module

    try:
        _ACTIVE = image
        file_cache.clear()
        for clear in clearers:
            clear()
        AstroidManager.ast_from_module_name = resolve
        yield
    finally:
        AstroidManager.ast_from_module_name = previous
        ast_cache.clear()
        ast_cache.update(saved_ast)
        file_cache.clear()
        file_cache.update(saved_files)
        try:
            for clear in clearers:
                clear()
        finally:
            _ACTIVE = None

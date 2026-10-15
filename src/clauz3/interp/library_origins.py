"""Authorize nontrusted library bindings without initializing their modules.

An import name grants no authority to the source found under that name. The
interpreter binds only verified fixed providers or inert namespace values after
this check; it must never initialize a module selected by an entry program.
"""

from __future__ import annotations

import site
import sys
import sysconfig
from collections.abc import Iterable
from importlib.machinery import BuiltinImporter, FrozenImporter, ModuleSpec, PathFinder
from pathlib import Path
from types import MethodType, ModuleType
from typing import Any, cast

from .errors import TrustedBoundaryError
from .import_bindings import (
    ImportComponent,
    ImportPlan,
    _find_spec,
    plan_import,
    resolve_path,
)


def _environment_roots() -> tuple[Path, ...]:
    paths = sysconfig.get_paths()
    roots = [
        Path(paths[key]).resolve()
        for key in ("stdlib", "platstdlib", "purelib", "platlib")
    ]
    if site.ENABLE_USER_SITE:
        user_site = site.getusersitepackages()
        roots.extend(
            Path(path).resolve()
            for path in ([user_site] if isinstance(user_site, str) else user_site)
        )
    # An editable framework install is not necessarily under site-packages.
    # Authorize this package, not the surrounding checkout or arbitrary sys.path.
    roots.append(Path(__file__).resolve().parents[1])
    return tuple(dict.fromkeys(roots))


# Capture during framework initialization, before per-program import roots are
# introduced. None of these authority roots comes from the submitted program.
_AUTHORITY_ROOTS = _environment_roots()
_NAMESPACE_PATH_TYPE = vars(sys.modules["_frozen_importlib_external"])["_NamespacePath"]
_NAMESPACE_FINDER = PathFinder._get_spec.__func__  # type: ignore[attr-defined]


def validate_library_module(
    name: str, *, import_roots: Iterable[Path] = ()
) -> ImportPlan:
    """Require an authorized complete import chain and consistent cached state.

    Finders inspect real files, so a poisoned cache cannot establish existence.
    Cached objects are checked through their concrete dictionaries, never via
    module attributes, custom import hooks, or dynamic namespace traversal.
    A verified cached namespace's live path snapshot and recalculation stamps
    are updated to the current validated plan; this is observable host state.
    """
    plan = plan_import(name, import_roots=import_roots)
    for component in plan.components:
        if component.origin is not None:
            _require_authorized(component.origin, requested=name)
        for path in component.locations or ():
            _require_authorized(path, requested=name)
        _check_cached(component, requested=name)
    return plan


def validate_trusted_module(
    name: str,
    *,
    source: Path,
    trusted_roots: Iterable[Path],
    import_roots: Iterable[Path],
) -> ImportPlan:
    """Check a complete trusted chain before importlib can initialize it.

    Namespace ancestors execute no code. Every file-backed component, including
    parent initializers, must instead have explicit trusted-root authority. An
    empty initializer outside those roots is not sufficient: Python may execute
    bytecode rather than the source that a static inspection would read.
    Verified cached namespaces have their live path snapshots and recalculation
    stamps updated to the current validated plan, preserving cached children.
    """
    plan = plan_trusted_module(
        name, source=source, trusted_roots=trusted_roots, import_roots=import_roots
    )
    for component in plan.components:
        try:
            _check_cached(component, requested=name)
        except TrustedBoundaryError as exc:
            raise TrustedBoundaryError(
                f"trusted module {name!r}: its cached import chain does not "
                f"match the configured trusted source ({exc})"
            ) from exc
    return plan


def plan_trusted_module(
    name: str,
    *,
    source: Path,
    trusted_roots: Iterable[Path],
    import_roots: Iterable[Path],
) -> ImportPlan:
    """Validate source authority without consulting or changing cached modules.

    The layer coordinator uses this before detaching a previous layer's cache.
    Cached-identity validation still runs after selecting the requested image.
    """
    roots = tuple(root.resolve() for root in trusted_roots)
    source = source.resolve()
    if not any(source.is_relative_to(root) for root in roots):
        raise TrustedBoundaryError(
            f"trusted module {name!r}: source {str(source)!r} is outside "
            "the configured trusted roots"
        )
    try:
        plan = plan_import(name, import_roots=import_roots)
    except ModuleNotFoundError as exc:
        raise TrustedBoundaryError(
            f"trusted module {name!r}: its configured source {str(source)!r} "
            "cannot be resolved without a shadowed or missing parent"
        ) from exc
    if plan.components[-1].origin != source:
        raise TrustedBoundaryError(
            f"trusted module {name!r}: selected source "
            f"{str(plan.components[-1].origin)!r} is not its configured "
            f"trusted source {str(source)!r}"
        )
    for component in plan.components:
        if component.kind != "namespace" and (
            component.origin is None
            or not any(component.origin.is_relative_to(root) for root in roots)
        ):
            raise TrustedBoundaryError(
                f"trusted module {name!r}: parent module {component.name!r} "
                "would execute outside the configured trusted roots; use "
                "namespace ancestors or explicitly trust the parent package"
            )
    return plan


def _require_authorized(path: Path, *, requested: str) -> None:
    if not any(path.is_relative_to(root) for root in _AUTHORITY_ROOTS):
        raise TrustedBoundaryError(
            f"nontrusted import {requested!r}: {str(path)!r} is outside "
            "authorized library origins; --allow-import grants a name, "
            "not permission to execute local source"
        )


def _cache_error(requested: str, component: str, detail: str) -> TrustedBoundaryError:
    return TrustedBoundaryError(
        f"nontrusted import {requested!r}: cached module {component!r} "
        f"does not match its verified library origin ({detail})"
    )


def _check_cached(component: ImportComponent, *, requested: str) -> None:
    if component.name not in sys.modules:
        return
    module = sys.modules[component.name]
    if type(module) is not ModuleType:
        raise _cache_error(requested, component.name, "not a standard module")
    # Calling module.__dict__ can invoke a subclass's __getattribute__.
    namespace: dict[str, Any] = ModuleType.__getattribute__(module, "__dict__")
    cached_name = namespace.get("__name__")
    names = {component.resolved_name}
    # This stdlib implementation deliberately publishes its public alias in
    # __name__ while its spec retains the private frozen-module identity.
    if component.resolved_name == "_collections_abc":
        names.add("collections.abc")
    if type(cached_name) is not str or cached_name not in names:
        raise _cache_error(requested, component.name, "module name")
    cached_file = namespace.get("__file__")
    if component.origin is None:
        if cached_file is not None:
            raise _cache_error(requested, component.name, "unexpected source file")
    elif type(cached_file) is not str or resolve_path(cached_file) != component.origin:
        raise _cache_error(requested, component.name, "source file")

    spec = namespace.get("__spec__")
    if type(spec) is not ModuleSpec:
        raise _cache_error(requested, component.name, "missing standard spec")
    metadata = vars(spec)
    if type(metadata) is not dict:
        raise _cache_error(requested, component.name, "nonstandard spec metadata")
    spec_name = metadata.get("name")
    if type(spec_name) is not str or spec_name != component.resolved_name:
        raise _cache_error(requested, component.name, "spec name")
    if component.kind in {"builtin", "frozen"}:
        expected_origin = "built-in" if component.kind == "builtin" else "frozen"
        loader = BuiltinImporter if component.kind == "builtin" else FrozenImporter
        if (
            type(metadata.get("origin")) is not str
            or metadata.get("origin") != expected_origin
            or metadata.get("loader") is not loader
            or namespace.get("__loader__") is not loader
        ):
            raise _cache_error(requested, component.name, "builtin/frozen loader")
    elif component.kind == "file":
        origin = metadata.get("origin")
        if type(origin) is not str or resolve_path(origin) != component.origin:
            raise _cache_error(requested, component.name, "spec source file")
    elif metadata.get("origin") is not None:
        raise _cache_error(requested, component.name, "namespace spec origin")

    if component.locations is None:
        if (
            "__path__" in namespace
            or metadata.get("submodule_search_locations") is not None
        ):
            raise _cache_error(requested, component.name, "unexpected package path")
        return
    package_path = namespace.get("__path__")
    spec_path = metadata.get("submodule_search_locations")
    if component.kind == "namespace" and (
        type(package_path) is _NAMESPACE_PATH_TYPE
        or type(spec_path) is _NAMESPACE_PATH_TYPE
    ):
        if package_path is not spec_path:
            raise _cache_error(
                requested, component.name, "namespace path/spec identity"
            )
        _revalidate_and_restamp_namespace_path(
            package_path, component=component, requested=requested
        )
    for paths in (package_path, spec_path):
        if _cached_paths(paths) != component.locations:
            raise _cache_error(requested, component.name, "package search path")


def _revalidate_and_restamp_namespace_path(
    value: object, *, component: ImportComponent, requested: str
) -> None:
    """Revalidate and mutate a cached namespace without invoking import hooks.

    A genuine namespace records its previous parent's search path. When those
    inputs still find namespace portions, the old snapshot must agree. Python
    otherwise retains the old snapshot; it grants no authority here and is
    replaced by the freshly validated current plan. Preserve the module and
    its child bindings: evicting a parent can orphan cached leaf modules.
    This writes the live ``_path``, ``_last_parent_path``, and ``_last_epoch``;
    the path strings become resolved paths from the plan.
    """
    state = object.__getattribute__(value, "__dict__")
    if (
        type(state) is not dict
        or any(type(key) is not str for key in state)
        or set(state)
        != {"_name", "_path", "_last_parent_path", "_last_epoch", "_path_finder"}
    ):
        raise _cache_error(requested, component.name, "namespace metadata")
    finder = state.get("_path_finder")
    old_parents = state.get("_last_parent_path")
    if (
        type(state.get("_name")) is not str
        or state["_name"] != component.name
        or type(finder) is not MethodType
        or finder.__self__ is not PathFinder
        or finder.__func__ is not _NAMESPACE_FINDER
        or type(old_parents) is not tuple
        or any(type(path) is not str for path in old_parents)
        or type(state.get("_path")) not in (list, tuple)
        or type(state.get("_last_epoch")) is not int
    ):
        raise _cache_error(
            requested, component.name, "namespace recalculation metadata"
        )
    old_paths = _cached_paths(state.get("_path"))
    old_locations = _namespace_locations(component.name, old_parents)
    if old_paths is None or (old_locations is not None and old_paths != old_locations):
        raise _cache_error(
            requested, component.name, "namespace historical search path"
        )

    parent_name, _, _ = component.name.rpartition(".")
    parent = sys.modules.get(parent_name or "sys")
    if type(parent) is not ModuleType or (not parent_name and parent is not sys):
        raise _cache_error(requested, component.name, "namespace parent module")
    parent_namespace = ModuleType.__getattribute__(parent, "__dict__")
    parent_paths = parent_namespace.get("__path__" if parent_name else "path")
    if type(parent_paths) is _NAMESPACE_PATH_TYPE:
        parent_state = object.__getattribute__(parent_paths, "__dict__")
        parent_paths = parent_state.get("_path") if type(parent_state) is dict else None
    if type(parent_paths) not in (list, tuple) or any(
        type(path) is not str for path in parent_paths
    ):
        raise _cache_error(requested, component.name, "namespace parent search path")
    current_parents = tuple(parent_paths)
    if _namespace_locations(component.name, current_parents) != component.locations:
        raise _cache_error(requested, component.name, "namespace current search path")
    epoch = vars(_NAMESPACE_PATH_TYPE).get("_epoch")
    if type(epoch) is not int:
        raise _cache_error(requested, component.name, "namespace epoch")
    state["_path"] = [str(path) for path in component.locations or ()]
    state["_last_parent_path"] = current_parents
    state["_last_epoch"] = epoch


def _namespace_locations(name: str, paths: tuple[str, ...]) -> tuple[Path, ...] | None:
    spec = _find_spec(name, paths)
    if spec is None or spec.loader is not None or spec.origin is not None:
        return None
    return _cached_paths(spec.submodule_search_locations)


def _cached_paths(value: object) -> tuple[Path, ...] | None:
    if type(value) is _NAMESPACE_PATH_TYPE:
        # Iterating _NamespacePath can re-enter PathFinder using a cached
        # parent's attributes. Its concrete snapshot needs no such execution.
        metadata = object.__getattribute__(value, "__dict__")
        if type(metadata) is not dict:
            return None
        value = metadata.get("_path")
    if type(value) not in (list, tuple):
        return None
    paths = cast(list[object] | tuple[object, ...], value)
    if any(type(path) is not str for path in paths):
        return None
    return tuple(dict.fromkeys(resolve_path(cast(str, path)) for path in paths))

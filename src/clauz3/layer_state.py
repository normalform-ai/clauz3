"""Select verified, reusable trusted-layer state for one serialized operation.

Displaced bindings are restored; newly verified nonconflicting modules remain
available to embedders. This is not a rollback of arbitrary trusted Python
mutations or a sandbox for trusted initializers.
"""

from __future__ import annotations

import ast
import hashlib
import os
import sys
import sysconfig
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib.machinery import BYTECODE_SUFFIXES, EXTENSION_SUFFIXES, ModuleSpec
from pathlib import Path
from types import BuiltinFunctionType, FunctionType, ModuleType
from typing import Any

from clauz3._engine_bootstrap import bootstrap_engine_dependencies
from clauz3._vendor.deal_solver._funcs._registry import (
    ENGINE_FUNCTIONS,
    FUNCTIONS,
    REGISTRATIONS,
    Registration,
)
from clauz3.interp.errors import TrustedBoundaryError
from clauz3.interp.import_bindings import (
    ImportComponent,
    ImportPlan,
    plan_import,
    resolution_phase,
    resolve_path,
)
from clauz3.interp.library_origins import (
    _AUTHORITY_ROOTS,
    _NAMESPACE_PATH_TYPE,
    _check_cached,
    plan_trusted_module,
)

LOCK_TIMEOUT = 5.0
MAX_DEPENDENCY_MODULES = 256
MAX_DEPENDENCY_AST_NODES = 500_000
DEPENDENCY_MODULE_LIMIT_ENV = "CLAUZ3_MAX_DEPENDENCY_MODULES"
DEPENDENCY_NODE_LIMIT_ENV = "CLAUZ3_MAX_DEPENDENCY_AST_NODES"
_LOCK = threading.RLock()
_ACTIVE: LayerImage | None = None
_MISSING = object()
_FRAMEWORK = Path(__file__).resolve().parent


class LayerStateError(RuntimeError):
    """A selected layer cannot safely reuse or replace process-global state."""


def _dependency_limit(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        limit = int(value)
    except ValueError as exc:
        raise LayerStateError(f"{name} must be a positive integer") from exc
    if limit <= 0:
        raise LayerStateError(f"{name} must be a positive integer")
    return limit


def _dependency_limits() -> tuple[int, int]:
    """Read operator-owned work limits; exceeding one never skips validation."""
    return (
        _dependency_limit(DEPENDENCY_MODULE_LIMIT_ENV, MAX_DEPENDENCY_MODULES),
        _dependency_limit(DEPENDENCY_NODE_LIMIT_ENV, MAX_DEPENDENCY_AST_NODES),
    )


def _dependency_node_limit_error() -> LayerStateError:
    return LayerStateError(
        "cached layer dependency source graph is too large; "
        f"increase {DEPENDENCY_NODE_LIMIT_ENV} in the process environment"
    )


@dataclass(frozen=True)
class _Key:
    roots: tuple[Path, ...]
    paths: tuple[Path, ...]


_IMAGES: dict[_Key, LayerImage] = {}


def _paths(paths: Iterable[str | Path]) -> tuple[Path, ...]:
    return tuple(dict.fromkeys(Path(path or ".").resolve() for path in paths))


def _namespace(module: object) -> dict[str, Any]:
    if type(module) is not ModuleType:
        return {}
    return ModuleType.__getattribute__(module, "__dict__")  # type: ignore[no-any-return]


def _origin(module: object) -> Path | None:
    value = _namespace(module).get("__file__")
    return resolve_path(value) if type(value) is str else None


@dataclass(frozen=True)
class _EngineModule:
    module: ModuleType
    plan: ImportPlan
    origin: Path | None
    metadata: tuple[tuple[object, ...], object, object, object]


def _engine_metadata(
    module: ModuleType,
) -> tuple[tuple[object, ...], object, object, object] | None:
    namespace = _namespace(module)
    spec = namespace.get("__spec__")
    if type(spec) is not ModuleSpec:
        return None
    info = vars(spec)
    names = (
        namespace.get("__name__"),
        namespace.get("__package__"),
        info.get("name"),
        info.get("origin"),
    )
    if any(value is not None and type(value) is not str for value in names):
        return None
    paths: list[tuple[str, ...] | None] = []
    for value in (namespace.get("__path__"), info.get("submodule_search_locations")):
        if value is None:
            paths.append(None)
        elif type(value) in (list, tuple) and all(type(part) is str for part in value):
            paths.append(tuple(value))
        else:
            return None
    return (
        (*names, "__path__" in namespace, *paths),
        spec,
        namespace.get("__loader__"),
        info.get("loader"),
    )


@resolution_phase()
def _capture_engine_modules() -> dict[str, _EngineModule]:
    """Freeze explicit engine dependencies, never arbitrary ambient packages."""
    config = sysconfig.get_paths()
    stdlib = _paths(config[key] for key in ("stdlib", "platstdlib"))
    installed = _paths(config[key] for key in ("purelib", "platlib"))
    packages = {"clauz3": _FRAMEWORK}
    for name in ("deal", "astroid", "z3"):
        origin = _origin(sys.modules.get(name))
        if origin is not None and any(
            origin.is_relative_to(root) for root in _AUTHORITY_ROOTS
        ):
            packages[name] = origin.parent
    result = {}
    for name, module in list(sys.modules.items()):
        if type(module) is not ModuleType:
            continue
        top = name.split(".")[0]
        origin = _origin(module)
        package = packages.get(top)
        if origin is not None:
            eligible = (package is not None and origin.is_relative_to(package)) or (
                top in sys.stdlib_module_names
                and any(origin.is_relative_to(root) for root in stdlib)
                and not any(origin.is_relative_to(root) for root in installed)
            )
        else:
            eligible = (
                top in sys.stdlib_module_names or name in sys.builtin_module_names
            )
        if not eligible:
            continue
        try:
            plan = plan_import(name, import_roots=())
        except ImportError:
            continue
        if plan.components[-1].origin != origin or (
            origin is None and plan.components[-1].kind not in {"builtin", "frozen"}
        ):
            continue
        metadata = _engine_metadata(module)
        if metadata is not None:
            result[name] = _EngineModule(module, plan, origin, metadata)
    return result


bootstrap_engine_dependencies()
_ENGINE_MODULES = _capture_engine_modules()


def _engine_dependency(
    name: str,
    module: object,
    image: LayerImage,
) -> ImportPlan | None:
    engine = _ENGINE_MODULES.get(name)
    if engine is None or engine.origin in image.inventory:
        return None
    if module is not engine.module:
        raise LayerStateError(f"cached engine dependency identity changed: {name}")
    metadata = _engine_metadata(engine.module)
    if (
        metadata is None
        or metadata[0] != engine.metadata[0]
        or any(
            actual is not saved
            for actual, saved in zip(metadata[1:], engine.metadata[1:], strict=True)
        )
        or _origin(module) != engine.origin
    ):
        raise LayerStateError(f"cached engine dependency metadata changed: {name}")
    try:
        plan = image.source_plan(name)
    except ImportError as exc:
        raise LayerStateError(
            f"cached engine dependency selection changed: {name}"
        ) from exc
    if plan != engine.plan:
        raise LayerStateError(f"cached engine dependency selection changed: {name}")
    return plan


def _digest(path: Path) -> bytes:
    try:
        return hashlib.sha256(path.read_bytes()).digest()
    except OSError as exc:
        raise LayerStateError(f"layer source is no longer readable: {path}") from exc


def _module_name(path: Path, paths: tuple[Path, ...]) -> str:
    for root in paths:
        if path.is_relative_to(root):
            parts = path.relative_to(root).with_suffix("").parts
            if all(part.isidentifier() for part in parts):
                name = ".".join(parts)
                return name.removesuffix(".__init__")
    raise LayerStateError(f"trusted source is not below an import root: {path}")


def _inventory(roots: tuple[Path, ...]) -> dict[Path, bytes]:
    result: dict[Path, bytes] = {}
    for root in roots:
        if not root.is_dir():
            raise LayerStateError(f"trusted root is not a directory: {root}")
        for path in sorted(root.rglob("*.py")):
            resolved = path.resolve()
            if not any(resolved.is_relative_to(allowed) for allowed in roots):
                raise LayerStateError(
                    f"trusted source escapes configured roots: {path}"
                )
            result[resolved] = _digest(resolved)
    return result


def _bundled_layer(path: Path) -> bool:
    """Bundled tool sources have layer lifetimes, not engine lifetimes."""
    return path.is_relative_to(_FRAMEWORK / "stdlib")


def _local(component: ImportComponent, image: LayerImage) -> bool:
    if component.name in image.declared:
        return True
    path = component.origin
    if component.name in _ENGINE_MODULES and path not in image.inventory:
        return False
    if path is not None:
        if path.is_relative_to(_FRAMEWORK):
            return path in image.inventory or _bundled_layer(path)
        return not any(path.is_relative_to(root) for root in _AUTHORITY_ROOTS)
    return component.kind == "namespace" and any(
        _bundled_layer(path)
        or not any(path.is_relative_to(root) for root in _AUTHORITY_ROOTS)
        for path in component.locations or ()
    )


@dataclass(frozen=True)
class _StarBinding:
    owner: ModuleType
    exporter: ModuleType
    bindings: tuple[tuple[str, object, object], ...]


@dataclass(frozen=True)
class _AliasBinding:
    owner: ModuleType
    value: FunctionType
    candidates: tuple[tuple[str, str, str, object, object], ...]


@dataclass(frozen=True)
class _DependencySource:
    plan: ImportPlan
    digest: bytes


@dataclass(frozen=True)
class _DependencyHistory:
    source: _DependencySource
    stars: tuple[tuple[tuple[str, str], _StarBinding], ...]
    aliases: tuple[tuple[tuple[str, str], _AliasBinding], ...]


@dataclass
class LayerImage:
    """Verified identities parked between proof, approval and execution."""

    roots: tuple[Path, ...]
    import_roots: tuple[Path, ...]
    paths: tuple[Path, ...]
    ambient_paths: tuple[Path, ...]
    inventory: dict[Path, bytes]
    declared: dict[str, ImportComponent]
    owned_modules: dict[str, ModuleType] = field(default_factory=dict)
    astroid_state: object | None = None
    _plans: dict[str, ImportPlan] = field(default_factory=dict)
    _sources: dict[Path, bytes] = field(default_factory=dict)
    _registrations: list[Registration] = field(default_factory=list)
    _registration_modules: dict[str, ModuleType] = field(default_factory=dict)
    _registration_plans: dict[str, dict[str, ImportPlan]] = field(default_factory=dict)
    _star_bindings: dict[tuple[str, str], _StarBinding] = field(default_factory=dict)
    _alias_bindings: dict[tuple[str, str], _AliasBinding] = field(default_factory=dict)
    _dependency_sources: dict[ModuleType, _DependencySource] = field(
        default_factory=dict
    )
    _verified_dependencies: dict[ModuleType, _DependencyHistory] = field(
        default_factory=dict
    )
    _has_verified_state: bool = field(default=False, repr=False)
    _before: _Snapshot | None = field(default=None, repr=False)
    _preexisting_modules: tuple[ModuleType, ...] = field(default=(), repr=False)
    _relations: dict[str, set[Any]] = field(default_factory=dict)
    _datatypes: dict[type, Any] = field(default_factory=dict)
    _synthetic_rows: dict[Any, type] = field(default_factory=dict)
    _namespace_states: list[tuple[object, dict[str, object]]] = field(
        default_factory=list
    )

    def source_plan(self, name: str) -> ImportPlan:
        # plan_import also includes ambient sys.path. Keep its complete search
        # window fixed even if trusted code temporarily edits that global.
        previous = list(sys.path)
        try:
            sys.path[:] = [str(path) for path in self.paths]
            return plan_import(name, import_roots=self.paths)
        finally:
            sys.path[:] = previous

    def profile_source_identity(self) -> dict[str, str]:
        """Content identities used by persistent approval preferences.

        Includes the complete selected inventory and resolved dependency sources;
        freshness checks run before handing identities to the profile verifier.
        """
        self._check_fresh()
        sources = {**self.inventory, **self._sources}
        for dependency in self._dependency_sources.values():
            origin = dependency.plan.components[-1].origin
            if origin is not None:
                sources[origin] = dependency.digest
        return {str(path): digest.hex() for path, digest in sorted(sources.items())}

    def remember_plan(self, plan: ImportPlan) -> None:
        previous = self._plans.get(plan.name)
        if previous is not None and previous != plan:
            raise LayerStateError(f"layer import selection changed: {plan.name}")
        self._plans[plan.name] = plan
        for component in plan.components:
            if component.origin is not None and _local(component, self):
                self._remember_file(component.origin)

    def remember_source(self, name: str, path: Path) -> None:
        plan = self.source_plan(name)
        if plan.components[-1].origin != path.resolve():
            raise LayerStateError(
                f"layer source does not match selected import: {name}"
            )
        self.remember_plan(plan)
        self._remember_file(path.resolve())

    def _remember_file(self, path: Path) -> None:
        digest = _digest(path)
        previous = self._sources.get(path)
        if previous is not None and previous != digest:
            raise LayerStateError(f"layer source changed; start a new process: {path}")
        self._sources[path] = digest

    @resolution_phase()
    def _check_fresh(self) -> None:
        if _inventory(self.roots) != self.inventory:
            raise LayerStateError(
                "trusted layer inventory changed; start a new process"
            )
        for name, plan in self._plans.items():
            try:
                current = self.source_plan(name)
            except ImportError as exc:
                raise LayerStateError(
                    f"layer import selection changed: {name}"
                ) from exc
            if current != plan:
                raise LayerStateError(f"layer import selection changed: {name}")
        for path, digest in self._sources.items():
            if _digest(path) != digest:
                raise LayerStateError(
                    f"layer source changed; start a new process: {path}"
                )

    def refresh_registrations(self) -> None:
        """Select registrations after checked trusted initializers have run.

        Newly imported verified plugin owners and explicit imports retained in
        layer globals are eligible. Direct writes to FUNCTIONS have no owner
        provenance and cannot establish authority, including on first use.
        """
        if _ACTIVE is not self or self._before is None:
            raise LayerStateError("registration refresh requires the active layer")
        _capture(self, self._before)
        _apply_function_records(self)

    @contextmanager
    def activate(self) -> Iterator[LayerImage]:
        """Resume this exact image, rechecking source freshness before use."""
        with _locked():
            if _ACTIVE is not None:
                if _ACTIVE is not self:
                    raise LayerStateError("incompatible nested trusted-layer selection")
                yield self
                return
            with _activate(self):
                yield self


@contextmanager
def _locked() -> Iterator[None]:
    if not _LOCK.acquire(timeout=LOCK_TIMEOUT):
        raise LayerStateError(
            "trusted-layer selection is busy; cross-thread callbacks cannot "
            "reenter an active operation"
        )
    try:
        yield
    finally:
        _LOCK.release()


@contextmanager
def selected_layer(
    *, trusted_roots: Iterable[Path] = (), import_paths: Iterable[Path] = ()
) -> Iterator[LayerImage]:
    """Select state without importing any trusted initializer.

    Equivalent effective search paths share an image. Nested callers normalize
    against the outer operation's original ambient path, not its temporary one.
    """
    with _locked():
        roots, imports = _paths(trusted_roots), _paths(import_paths)
        ambient = (
            _ACTIVE.ambient_paths
            if _ACTIVE is not None
            else _paths(path for path in sys.path if type(path) is str)
        )
        paths = _paths((*imports, *ambient))
        key = _Key(roots, paths)
        if _ACTIVE is not None:
            if _Key(_ACTIVE.roots, _ACTIVE.paths) != key:
                raise LayerStateError("incompatible nested trusted-layer selection")
            yield _ACTIVE
            return
        image = _IMAGES.get(key)
        if image is None:
            with resolution_phase():
                inventory = _inventory(roots)
                components: dict[str, ImportComponent] = {}
                plans: list[ImportPlan] = []
                try:
                    for source in inventory:
                        name = _module_name(source, imports)
                        if name == "__init__":
                            # Existing loader checks bare initializer grammar.
                            continue
                        plan = plan_trusted_module(
                            name, source=source, trusted_roots=roots, import_roots=paths
                        )
                        components.update((item.name, item) for item in plan.components)
                        plans.append(plan)
                except (TrustedBoundaryError, ImportError) as exc:
                    raise LayerStateError(str(exc)) from exc
                image = LayerImage(
                    roots, imports, paths, ambient, inventory, components
                )
                for plan in plans:
                    image.remember_plan(plan)
                _IMAGES[key] = image
        try:
            with _activate(image):
                yield image
        finally:
            # A failed first capture can retain provisional Python/AST objects
            # that global restoration removed. Retry from a fresh image after
            # an operator fixes its configuration; keep verified images parked.
            if not image._has_verified_state and _IMAGES.get(key) is image:
                del _IMAGES[key]


@dataclass
class _Snapshot:
    modules: dict[str, Any]
    namespaces: dict[str, dict[str, Any]]
    namespace_states: list[tuple[object, dict[str, object]]]
    functions: dict[str, Any]
    contracts: set[str]
    relations: dict[str, set[Any]]
    datatypes: dict[type, Any]
    synthetic_rows: dict[Any, type]
    path: list[str]


def _namespace_states(
    modules: Iterable[object],
) -> list[tuple[object, dict[str, object]]]:
    states: list[tuple[object, dict[str, object]]] = []
    seen: set[int] = set()
    for module in modules:
        path = _namespace(module).get("__path__")
        if type(path) is not _NAMESPACE_PATH_TYPE or id(path) in seen:
            continue
        seen.add(id(path))
        state = object.__getattribute__(path, "__dict__")
        if type(state) is dict and all(type(key) is str for key in state):
            states.append(
                (
                    path,
                    {
                        key: list(value) if type(value) is list else value
                        for key, value in state.items()
                    },
                )
            )
    return states


def _restore_namespace_states(states: list[tuple[object, dict[str, object]]]) -> None:
    for path, saved in states:
        state = object.__getattribute__(path, "__dict__")
        state.clear()
        state.update(
            {
                key: list(value) if type(value) is list else value
                for key, value in saved.items()
            }
        )


def _snapshot() -> _Snapshot:
    from clauz3 import spec
    from clauz3._vendor.deal_solver._proxies import _row

    modules = dict(sys.modules)
    return _Snapshot(
        modules=modules,
        namespaces={name: dict(_namespace(module)) for name, module in modules.items()},
        namespace_states=_namespace_states(modules.values()),
        functions=dict(FUNCTIONS),
        contracts=set(spec.REGISTERED_CONTRACTS),
        relations={key: set(values) for key, values in spec._RELATIONS.items()},
        datatypes=dict(_row._DATATYPE_CACHE),
        synthetic_rows=dict(_row._SYNTHETIC_ROW_CACHE),
        path=list(sys.path),
    )


def _matching(name: str, module: object, image: LayerImage) -> ImportPlan | None:
    try:
        plan = image.source_plan(name)
        for component in plan.components:
            _check_cached(component, requested=name)
        if sys.modules.get(name) is not module:
            return None
        return plan
    except (ImportError, TrustedBoundaryError):
        return None


def _detach(name: str) -> None:
    module = sys.modules.get(name)
    origin = _origin(module)
    engine = _ENGINE_MODULES.get(name)
    if (
        engine is not None
        and module is engine.module
        and not any(origin in image.inventory for image in _IMAGES.values())
    ):
        raise LayerStateError(f"cannot replace an initialized engine module: {name}")
    if origin is not None and any(
        str(origin).endswith(ext) for ext in EXTENSION_SUFFIXES
    ):
        raise LayerStateError(f"cannot replace an initialized extension module: {name}")
    if (
        origin is not None
        and origin.is_relative_to(_FRAMEWORK)
        and not _bundled_layer(origin)
        and not any(origin in image.inventory for image in _IMAGES.values())
    ):
        raise LayerStateError(f"cannot replace an initialized framework module: {name}")
    sys.modules.pop(name, None)
    parent_name, _, child = name.rpartition(".")
    parent = _namespace(sys.modules.get(parent_name))
    if parent.get(child, _MISSING) is module:
        parent.pop(child, None)


def _defined_module(value: object) -> str | None:
    if type(value) in (FunctionType, BuiltinFunctionType):
        name = value.__module__
    elif isinstance(value, type):
        # Use the built-in descriptor directly, bypassing custom metaclass
        # attribute hooks (Enum and ABC classes also carry real provenance).
        name = type.__dict__["__module__"].__get__(value)
    else:
        return None
    return name if type(name) is str else None


def _exported_identity(module: object, value: object) -> bool:
    """Inspect concrete exports without descriptors, equality, or module hooks."""
    return any(exported is value for exported in _namespace(module).values())


def _definition_owner(
    value: object,
    imports: dict[str, Any],
    image: LayerImage,
    *,
    current_name: str,
) -> tuple[str, object] | None:
    declared = _defined_module(value)
    if declared is None:
        return None
    owner = imports.get(declared)
    if type(owner) is not ModuleType:
        return declared, owner
    if type(value) is FunctionType:
        pending = value
        seen: set[int] = set()
        while True:
            if id(pending) in seen or len(seen) >= 64:
                raise LayerStateError("cached layer binding has cyclic wrappers")
            seen.add(id(pending))
            namespace = pending.__globals__
            defining_name = namespace.get("__name__")
            defining_module = (
                imports.get(defining_name) if type(defining_name) is str else None
            )
            if type(defining_name) is not str or (
                type(defining_module) is not ModuleType
                or _namespace(defining_module) is not namespace
            ):
                raise LayerStateError(
                    f"cached layer binding does not match selected owner: {declared}"
                )
            if defining_name != declared:
                # A genuine decorator/host patch can have different globals
                # from its wrapped provider. Verify that defining identity and
                # selected source, without adopting unrelated host state.
                plan = _engine_dependency(defining_name, defining_module, image)
                if plan is None:
                    plan = _matching(defining_name, defining_module, image)
                if plan is None:
                    raise LayerStateError(
                        "cached layer binding does not match selected owner: "
                        f"{defining_name}"
                    )
                image.remember_plan(plan)
            wrapped = pending.__dict__.get("__wrapped__", _MISSING)
            if wrapped is _MISSING:
                break
            if type(wrapped) is not FunctionType:
                raise LayerStateError("cached layer binding has unsupported wrapper")
            pending = wrapped
        if declared != current_name and not _exported_identity(owner, value):
            # Captured engine factories legitimately return nested functions
            # that are not module exports (for example bare deal.has/chain).
            # Their real globals and current selected engine plan still have
            # to match; external owner namespaces retain the export check.
            engine_plan = (
                _engine_dependency(declared, owner, image)
                if value.__globals__ is _namespace(owner)
                else None
            )
            if engine_plan is None:
                raise LayerStateError(
                    f"cached layer binding does not match selected export: {declared}"
                )
            image.remember_plan(engine_plan)
    elif not _exported_identity(owner, value):
        raise LayerStateError(
            f"cached layer binding does not match selected export: {declared}"
        )
    return declared, owner


def _check_from_binding(
    value: object,
    exporter: object,
    imported_name: str,
    *,
    current_name: str,
    prefix: str,
) -> None:
    # A local definition can replace a same-named imported/fallback binding.
    # Its concrete owner was verified above; the earlier import is not its
    # provenance. Scalars have no defining namespace and cannot use this rule.
    if _defined_module(value) == current_name:
        return
    if _namespace(exporter).get(imported_name, _MISSING) is not value:
        raise LayerStateError(
            "cached layer binding does not match selected export: "
            f"{prefix}.{imported_name}"
        )


def _source_aliases(tree: ast.Module) -> dict[str, set[str]]:
    """Find explicit module-level aliases without evaluating initializer code.

    Source imports are historical candidates. A later alias can replace one
    with a different verified function, but an unrelated equal binding alone
    cannot establish that replacement's provenance.
    """
    aliases: dict[str, set[str]] = {}
    pending: list[ast.AST] = [tree]
    while pending:
        node = pending.pop()
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            continue
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        source = None
        if isinstance(value, ast.Name):
            source = value.id
        elif isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            source = f"{value.value.id}.{value.attr}"
        elif (
            isinstance(value, ast.Subscript)
            and isinstance(value.value, ast.Call)
            and isinstance(value.value.func, ast.Name)
            and value.value.func.id == "globals"
            and not value.value.args
            and not value.value.keywords
            and isinstance(value.slice, ast.Constant)
            and type(value.slice.value) is str
        ):
            source = value.slice.value
        if source is not None:
            for target in targets:
                if isinstance(target, ast.Name):
                    aliases.setdefault(target.id, set()).add(source)
        pending.extend(ast.iter_child_nodes(node))
    return aliases


def _check_alias_binding(
    name: str,
    module: ModuleType,
    bound: str,
    value: FunctionType,
    candidates: dict[str, list[tuple[str, str, object]]],
    aliases: set[str],
    image: LayerImage,
    pending: dict[tuple[str, str], _AliasBinding],
    *,
    fresh: bool,
) -> None:
    key = (name, bound)
    previous = pending.get(key, image._alias_bindings.get(key))
    if previous is None and not fresh:
        raise LayerStateError(
            f"cached layer binding has no verified alias history: {name}.{bound}"
        )
    observed = tuple(
        (
            candidate,
            prefix,
            imported_name,
            exporter,
            _namespace(exporter).get(imported_name, _MISSING),
        )
        for candidate in sorted({bound, *aliases})
        for prefix, imported_name, exporter in candidates[candidate]
    )
    if previous is not None:
        if (
            previous.owner is not module
            or previous.value is not value
            or len(previous.candidates) != len(observed)
            or any(
                old[:3] != new[:3] or old[3] is not new[3] or old[4] is not new[4]
                for old, new in zip(previous.candidates, observed, strict=True)
            )
        ):
            raise LayerStateError(
                f"cached layer binding alias history changed: {name}.{bound}"
            )
    else:
        pending[key] = _AliasBinding(module, value, observed)


def _check_star_binding(
    name: str,
    module: ModuleType,
    prefix: str,
    exporter: object,
    image: LayerImage,
    pending: dict[tuple[str, str], _StarBinding],
    *,
    fresh: bool,
    budget: list[int],
) -> None:
    key = (name, prefix)
    previous = pending.get(key, image._star_bindings.get(key))
    if previous is None and not fresh:
        # A replaced exporter may have removed names from __all__. Its current
        # exports cannot reconstruct what an ambient star import retained.
        raise LayerStateError(
            f"cached layer star binding has no verified history: {name}"
        )
    if type(exporter) is not ModuleType:
        raise LayerStateError(
            f"cached layer star binding has no verified exporter: {prefix}"
        )
    exports = _namespace(exporter)
    names = exports.get("__all__", _MISSING)
    if names is _MISSING:
        names = [key for key in exports if type(key) is str and not key.startswith("_")]
    if type(names) not in (list, tuple) or any(type(key) is not str for key in names):
        raise LayerStateError(
            f"cached layer star binding has unsupported exports: {prefix}"
        )
    budget[0] -= len(names)
    if budget[0] < 0:
        raise _dependency_node_limit_error()
    namespace = _namespace(module)
    bindings = tuple(
        (key, exports.get(key, _MISSING), namespace.get(key, _MISSING)) for key in names
    )
    if previous is not None:
        if (
            previous.owner is not module
            or previous.exporter is not exporter
            or len(previous.bindings) != len(bindings)
            or any(
                old[0] != new[0] or old[1] is not new[1] or old[2] is not new[2]
                for old, new in zip(previous.bindings, bindings, strict=True)
            )
        ):
            raise LayerStateError(f"cached layer star binding changed: {name}")
    else:
        for imported_name, exported, value in bindings:
            if exported is _MISSING or value is _MISSING:
                raise LayerStateError(
                    f"cached layer star binding is missing: {prefix}.{imported_name}"
                )
            _check_from_binding(
                value, exporter, imported_name, current_name=name, prefix=prefix
            )
        pending[key] = _StarBinding(module, exporter, bindings)


def _source_imports(
    tree: ast.Module, budget: list[int]
) -> Iterator[tuple[ast.Import | ast.ImportFrom, set[str] | None]]:
    """Identify imports that can bind module globals, counting every AST node.

    A local import cannot borrow a coincidentally same-named global binding as
    evidence that it executed. Nested scopes contribute only their explicitly
    declared globals; each declaration belongs to that scope alone.
    """
    pending: list[tuple[ast.AST, ast.AST]] = [(tree, tree)]
    globals_by_scope: dict[ast.AST, set[str]] = {}
    imports: list[tuple[ast.Import | ast.ImportFrom, ast.AST]] = []
    while pending:
        node, scope = pending.pop()
        budget[0] -= 1
        if budget[0] < 0:
            raise _dependency_node_limit_error()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = node
        if isinstance(node, ast.Global):
            globals_by_scope.setdefault(scope, set()).update(node.names)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append((node, scope))
        pending.extend((child, scope) for child in ast.iter_child_nodes(node))
    for node, scope in imports:
        global_names = None if scope is tree else globals_by_scope.get(scope, set())
        if global_names is not None:
            bound_names = (
                alias.asname
                or (
                    alias.name.split(".")[0]
                    if isinstance(node, ast.Import)
                    else alias.name
                )
                for alias in node.names
            )
            if not any(bound in global_names for bound in bound_names):
                continue
        yield node, global_names


def _committed_dependency(module: ModuleType) -> _DependencyHistory | None:
    """Find evidence committed after an entire successful layer operation.

    Name equality cannot transfer history to another module object. Pending
    records from refresh_registrations or an aborted operation are not shared.
    """
    for previous in _IMAGES.values():
        history = previous._verified_dependencies.get(module)
        if history is not None:
            return history
    return None


def _check_dependency_source(
    name: str,
    module: ModuleType,
    origin: Path,
    source: bytes,
    image: LayerImage,
    sources: dict[ModuleType, _DependencySource],
    star_bindings: dict[tuple[str, str], _StarBinding],
    alias_bindings: dict[tuple[str, str], _AliasBinding],
) -> None:
    plan = image.source_plan(name)
    if plan.components[-1].origin != origin:
        raise LayerStateError(f"layer source does not match selected import: {name}")
    observed = _DependencySource(plan, hashlib.sha256(source).digest())
    history = _committed_dependency(module)
    previous = sources.get(module, image._dependency_sources.get(module))
    for saved in (previous, None if history is None else history.source):
        if saved is None:
            continue
        if saved.plan != observed.plan:
            raise LayerStateError(f"cached dependency import selection changed: {name}")
        if saved.digest != observed.digest:
            raise LayerStateError(
                f"cached dependency source changed; start a new process: {origin}"
            )
    sources[module] = observed
    if history is not None:
        for (_, bound), alias in history.aliases:
            # A changed binding may now match its original import directly,
            # so parsing alone would no longer invoke _check_alias_binding.
            if _namespace(module).get(bound, _MISSING) is not alias.value or any(
                _namespace(exporter).get(imported_name, _MISSING) is not exported
                for _, _, imported_name, exporter, exported in alias.candidates
            ):
                raise LayerStateError(
                    f"cached layer binding alias history changed: {name}.{bound}"
                )
        # These are historical witnesses, not permission to bypass the walk.
        # Current exports, retained objects and their full closure are checked
        # below under this image's selected import paths.
        star_bindings.update(history.stars)
        alias_bindings.update(history.aliases)


def _retained_dependencies(
    name: str,
    module: ModuleType,
    imports: dict[str, Any],
    budget: list[int],
    image: LayerImage,
    star_bindings: dict[tuple[str, str], _StarBinding],
    alias_bindings: dict[tuple[str, str], _AliasBinding],
    sources: dict[ModuleType, _DependencySource],
    *,
    fresh: bool,
) -> dict[str, object]:
    origin = _origin(module)
    if origin is not None and str(origin).endswith(tuple(BYTECODE_SUFFIXES)):
        raise LayerStateError(f"cached layer source is not inspectable: {name}")
    namespace = _namespace(module)
    dependencies: dict[str, object] = {}
    for value in namespace.values():
        if type(value) is ModuleType:
            metadata = _namespace(value)
            dependency = metadata.get("__name__")
            spec = metadata.get("__spec__")
            if type(spec) is ModuleSpec:
                canonical = vars(spec).get("name")
                if type(canonical) is str and imports.get(canonical) is value:
                    dependency = canonical
            if type(dependency) is str and dependency != name:
                dependencies[dependency] = value
        else:
            definition = _definition_owner(value, imports, image, current_name=name)
            if definition is not None and definition[0] != name:
                dependency, owner = definition
                dependencies[dependency] = owner
    origin = _origin(module)
    if origin is not None and origin.suffix == ".py":
        try:
            source = origin.read_bytes()
        except OSError as exc:
            raise LayerStateError(
                f"cannot inspect cached layer source: {origin}"
            ) from exc
        # Check before parsing: changed source could remove the star/alias
        # declaration and otherwise avoid the binding-history checks entirely.
        _check_dependency_source(
            name, module, origin, source, image, sources, star_bindings, alias_bindings
        )
        try:
            tree = ast.parse(source, filename=str(origin))
        except SyntaxError as exc:
            raise LayerStateError(
                f"cannot inspect cached layer source: {origin}"
            ) from exc
        package = name if "__path__" in namespace else name.rpartition(".")[0]
        candidates: dict[str, list[tuple[str, str, object]]] = {}
        for node, global_names in _source_imports(tree, budget):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    if global_names is not None and bound not in global_names:
                        continue
                    if bound in namespace:
                        dependencies.setdefault(alias.name, imports.get(alias.name))
            elif isinstance(node, ast.ImportFrom):
                last_aliases = {
                    alias.asname or alias.name: alias for alias in node.names
                }
                prefix = node.module or ""
                if node.level:
                    ancestors = package.split(".")
                    if node.level > len(ancestors):
                        raise LayerStateError(
                            f"cannot resolve cached relative import: {name}"
                        )
                    prefix = ".".join(
                        (*ancestors[: len(ancestors) - node.level + 1], prefix)
                    ).rstrip(".")
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if global_names is not None and bound not in global_names:
                        continue
                    if bound not in namespace and bound != "*":
                        continue
                    value = namespace.get(bound)
                    if type(value) is FunctionType and last_aliases[bound] is not alias:
                        # One ImportFrom binds its aliases left to right. Only
                        # its final same-name function binding can survive.
                        continue
                    # An inactive fallback can bind the same name as a live
                    # import or local definition. Concrete callable/class
                    # provenance above identifies the actual retained owner;
                    # an absent fallback is not another dependency. Scalars
                    # have no such provenance and must still fail closed.
                    if prefix not in imports and _defined_module(value) is not None:
                        continue
                    if prefix in imports and alias.name == "*":
                        _check_star_binding(
                            name,
                            module,
                            prefix,
                            imports[prefix],
                            image,
                            star_bindings,
                            fresh=fresh,
                            budget=budget,
                        )
                    elif prefix in imports:
                        candidates.setdefault(bound, []).append(
                            (prefix, alias.name, imports[prefix])
                        )
                    if prefix:
                        dependencies.setdefault(prefix, imports.get(prefix))
                    child = f"{prefix}.{alias.name}" if prefix else alias.name
                    if child in imports:
                        dependencies.setdefault(child, imports[child])
        verified_bindings = {
            bound
            for bound, choices in candidates.items()
            if all(
                _namespace(exporter).get(imported_name, _MISSING) is namespace[bound]
                for _, imported_name, exporter in choices
            )
        }
        aliases: dict[str, set[str]] | None = None
        for bound, choices in list(candidates.items()):
            value = namespace[bound]
            if type(value) is FunctionType:
                if bound in verified_bindings:
                    continue
                if any(
                    _namespace(exporter).get(imported_name, _MISSING) is value
                    for _, imported_name, exporter in choices
                ):
                    # Different source statements are historical candidates,
                    # not evidence that an old ambient binding is current.
                    _check_alias_binding(
                        name,
                        module,
                        bound,
                        value,
                        candidates,
                        set(),
                        image,
                        alias_bindings,
                        fresh=fresh,
                    )
                    continue
                if aliases is None:
                    aliases = _source_aliases(tree)
                selected_aliases = {
                    alias
                    for alias in aliases.get(bound, ())
                    if alias in verified_bindings and namespace[alias] is value
                }
                for source_alias in aliases.get(bound, ()):
                    base, dot, member = source_alias.partition(".")
                    provider = namespace.get(base)
                    if (
                        dot
                        and type(provider) is ModuleType
                        and _namespace(provider).get(member, _MISSING) is value
                    ):
                        provider_name = _namespace(provider).get("__name__")
                        if type(provider_name) is str:
                            candidates[source_alias] = [
                                (provider_name, member, provider)
                            ]
                            selected_aliases.add(source_alias)
                if selected_aliases:
                    _check_alias_binding(
                        name,
                        module,
                        bound,
                        value,
                        candidates,
                        selected_aliases,
                        image,
                        alias_bindings,
                        fresh=fresh,
                    )
                    continue
            for prefix, imported_name, exporter in choices:
                _check_from_binding(
                    value, exporter, imported_name, current_name=name, prefix=prefix
                )
    return dependencies


def _verify_ambient_dependencies(
    name: str,
    module: ModuleType,
    image: LayerImage,
    *,
    original_modules: dict[str, Any] | None = None,
) -> dict[str, ImportPlan]:
    """Validate the entire retained import closure before adopting a module.

    Matching one filename cannot establish the origins of transitive imported
    globals. Each retained source-backed dependency is visited once, including
    installed plugin carriers; framework internals have their separately fixed
    engine authority. Module and source-node budgets bound cycles and work.
    """
    pending = [(name, module)]
    seen: set[int] = set()
    plans: dict[str, ImportPlan] = {}
    star_bindings: dict[tuple[str, str], _StarBinding] = {}
    alias_bindings: dict[tuple[str, str], _AliasBinding] = {}
    sources: dict[ModuleType, _DependencySource] = {}
    module_limit, node_limit = _dependency_limits()
    budget = [node_limit]
    while pending:
        current_name, current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if len(seen) > module_limit:
            raise LayerStateError(
                "cached layer dependency graph is too large; "
                f"increase {DEPENDENCY_MODULE_LIMIT_ENV} in the process environment"
            )
        imports = (
            original_modules
            if original_modules is not None
            and original_modules.get(current_name) is current
            else sys.modules
        )
        dependencies = _retained_dependencies(
            current_name,
            current,
            imports,
            budget,
            image,
            star_bindings,
            alias_bindings,
            sources,
            fresh=(
                original_modules is not None
                and original_modules.get(current_name) is not current
                and not any(
                    current is previous for previous in image._preexisting_modules
                )
            ),
        )
        for dependency, retained in dependencies.items():
            if retained is None or sys.modules.get(dependency) is not retained:
                raise LayerStateError(
                    f"cached layer dependency has no verified identity: {dependency}"
                )
            engine_plan = _engine_dependency(dependency, retained, image)
            if engine_plan is not None:
                image.remember_plan(engine_plan)
                plans[dependency] = engine_plan
                continue
            plan = _matching(dependency, retained, image)
            if plan is None:
                raise LayerStateError(
                    "cached layer dependency disagrees with selected source: "
                    f"{dependency}"
                )
            image.remember_plan(plan)
            plans[dependency] = plan
            if type(retained) is ModuleType and _local(plan.components[-1], image):
                image.owned_modules[dependency] = retained
            origin = plan.components[-1].origin
            if origin is not None and str(origin).endswith(tuple(BYTECODE_SUFFIXES)):
                raise LayerStateError(
                    f"cached layer source is not inspectable: {dependency}"
                )
            if (
                type(retained) is ModuleType
                and origin is not None
                and origin.suffix == ".py"
                and (
                    not origin.is_relative_to(_FRAMEWORK)
                    or origin in image.inventory
                    or _bundled_layer(origin)
                )
            ):
                pending.append((dependency, retained))
    # A partially checked graph must never authorize subsequent ambient reuse.
    image._star_bindings.update(star_bindings)
    image._alias_bindings.update(alias_bindings)
    image._dependency_sources.update(sources)
    return plans


@resolution_phase()
def _select_python(image: LayerImage) -> None:
    wanted = set(image.declared) | set(image.owned_modules)
    previous_owned = {
        name for other in _IMAGES.values() for name in other.owned_modules
    }
    remove: set[str] = set()
    for name, module in list(sys.modules.items()):
        origin = _origin(module)
        inspect = (
            name in wanted
            or name in previous_owned
            or any(
                root.joinpath(*name.split(".")).is_dir()
                or root.joinpath(*name.split(".")).with_suffix(".py").is_file()
                for root in image.import_roots
            )
            or (
                origin is not None
                and not any(origin.is_relative_to(root) for root in _AUTHORITY_ROOTS)
            )
        )
        if not inspect:
            continue
        selected = image.owned_modules.get(name)
        if selected is not None and module is not selected:
            remove.add(name)
            continue
        if selected is None and any(
            other is not image and other.owned_modules.get(name) is module
            for other in _IMAGES.values()
        ):
            # Module globals retain an entire dependency graph, including
            # from-imported scalars with no runtime origin metadata. A new
            # effective-key image gets its own initialization, even where a
            # module's own filename happens to be shared by both images.
            remove.add(name)
            continue
        try:
            source = image.source_plan(name).components[-1]
        except ImportError:
            if name in wanted or name in previous_owned:
                remove.add(name)
            continue
        if (
            name not in wanted
            and name not in previous_owned
            and (
                source.origin is None
                or not any(
                    source.origin.is_relative_to(root) for root in image.import_roots
                )
            )
        ):
            continue
        plan = _matching(name, module, image)
        if plan is None:
            remove.add(name)
        elif name in wanted and _local(plan.components[-1], image):
            image.remember_plan(plan)
            if type(module) is ModuleType:
                if selected is None:
                    _verify_ambient_dependencies(name, module, image)
                image.owned_modules[name] = module
    # A replaced package cannot leave children pointing into its old graph.
    for name in list(sys.modules):
        if any(name.startswith(parent + ".") for parent in remove):
            remove.add(name)
    for name in sorted(remove, key=lambda item: item.count("."), reverse=True):
        _detach(name)
    _restore_namespace_states(image._namespace_states)
    for name, module in sorted(
        image.owned_modules.items(), key=lambda pair: pair[0].count(".")
    ):
        sys.modules[name] = module
        parent_name, _, child = name.rpartition(".")
        parent = _namespace(sys.modules.get(parent_name))
        if parent:
            parent[child] = module
    for name in image.owned_modules:
        if _matching(name, sys.modules[name], image) is None:
            raise LayerStateError(
                f"selected module no longer matches its source: {name}"
            )


def _records(image: LayerImage) -> list[Registration]:
    owners = (*image.owned_modules.values(), *image._registration_modules.values())
    return [
        record
        for record in REGISTRATIONS
        if record.module is not None
        and record.origin is not None
        and any(record.module is module for module in owners)
        and _origin(record.module) == record.origin
    ]


@resolution_phase()
def _select_registration_modules(image: LayerImage, before: _Snapshot) -> None:
    module_limit, _ = _dependency_limits()
    candidates = [
        record
        for record in REGISTRATIONS
        if record.module is not None
        and record.origin is not None
        and ENGINE_FUNCTIONS.get(record.name) is not record.model
    ]
    selected = dict(image._registration_modules)
    pending = list(image.owned_modules.values())
    seen: set[int] = set()
    while pending:
        module = pending.pop()
        if id(module) in seen:
            continue
        seen.add(id(module))
        if len(seen) > module_limit:
            raise LayerStateError(
                "selected registration dependency graph is too large; "
                f"increase {DEPENDENCY_MODULE_LIMIT_ENV} in the process environment"
            )
        values = tuple(_namespace(module).values())
        imported_modules: list[tuple[ModuleType, str]] = []
        if candidates:
            for value in values:
                if type(value) is not ModuleType:
                    continue
                imported_name = _namespace(value).get("__name__")
                origin = _origin(value)
                if type(imported_name) is str and (
                    origin is None
                    or not origin.is_relative_to(_FRAMEWORK)
                    or _bundled_layer(origin)
                ):
                    imported_modules.append((value, imported_name))
        for record in candidates:
            owner = record.module
            assert owner is not None
            name = _namespace(owner).get("__name__")
            if type(name) is not str:
                continue
            if any(value is record.owner or value is record.model for value in values):
                selected[name] = owner
            for value, imported_name in imported_modules:
                if name == imported_name or name.startswith(imported_name + "."):
                    pending.append(value)
                    if value is owner:
                        selected[name] = owner
    for record in candidates:
        recorded_module = record.module
        assert recorded_module is not None
        name = _namespace(recorded_module).get("__name__")
        if (
            type(name) is str
            and sys.modules.get(name) is recorded_module
            and before.modules.get(name, _MISSING) is not recorded_module
        ):
            selected[name] = recorded_module
    image._registration_modules.clear()
    for name, module in selected.items():
        if sys.modules.get(name) is not module:
            continue
        plan = _matching(name, module, image)
        if plan is None:
            continue
        origin = plan.components[-1].origin
        if origin is None:
            continue
        for previous in _IMAGES.values():
            if previous._registration_modules.get(name) is not module:
                continue
            for dependency, previous_plan in previous._registration_plans.get(
                name, {}
            ).items():
                try:
                    current_plan = image.source_plan(dependency)
                except ImportError as exc:
                    raise LayerStateError(
                        f"selected plugin dependency changed: {dependency}"
                    ) from exc
                if current_plan != previous_plan:
                    raise LayerStateError(
                        f"selected plugin dependency changed: {dependency}"
                    )
        dependencies = _verify_ambient_dependencies(
            name,
            module,
            image,
            original_modules=before.modules,
        )
        image.remember_source(name, origin)
        image._registration_modules[name] = module
        image._registration_plans[name] = dependencies


def _apply_function_records(image: LayerImage) -> None:
    from clauz3 import spec

    FUNCTIONS.clear()
    FUNCTIONS.update(ENGINE_FUNCTIONS)
    spec.REGISTERED_CONTRACTS.clear()
    for record in image._registrations:
        FUNCTIONS[record.name] = record.model
        if record.is_contract:
            spec.REGISTERED_CONTRACTS.add(record.name)


def _install_registries(image: LayerImage, before: _Snapshot) -> None:
    from clauz3 import spec
    from clauz3._vendor.deal_solver._proxies import _row

    _select_registration_modules(image, before)
    image._registrations = _records(image)
    # A matching layer may have been imported before its first operation.
    # Adopt its already-created relations and Row identities without replaying
    # decorators, while keeping other ambient modules out of selected state.
    origins = {str(_origin(module)) for module in image.owned_modules.values()}
    for origin, relations in before.relations.items():
        if origin in origins:
            image._relations.setdefault(origin, set()).update(relations)
    for key, schema in before.synthetic_rows.items():
        if type(key) is tuple and len(key) >= 2 and key[1] in origins:
            image._synthetic_rows[key] = schema
    for schema, datatype in before.datatypes.items():
        module_name = vars(schema).get("__module__")
        module = (
            image.owned_modules.get(module_name) if type(module_name) is str else None
        )
        if (
            module is not None
            and any(schema is value for value in vars(module).values())
        ) or schema in image._synthetic_rows.values():
            image._datatypes[schema] = datatype
    _apply_function_records(image)
    spec._RELATIONS.clear()
    spec._RELATIONS.update({key: set(value) for key, value in image._relations.items()})
    _row._DATATYPE_CACHE.clear()
    _row._DATATYPE_CACHE.update(image._datatypes)
    _row._SYNTHETIC_ROW_CACHE.clear()
    _row._SYNTHETIC_ROW_CACHE.update(image._synthetic_rows)


@resolution_phase()
def _capture(image: LayerImage, before: _Snapshot) -> set[str]:
    from clauz3 import spec
    from clauz3._vendor.deal_solver._proxies import _row

    verified: set[str] = set()
    for name, module in list(sys.modules.items()):
        if (
            name not in image.owned_modules
            and before.modules.get(name, _MISSING) is module
        ):
            continue
        plan = _matching(name, module, image)
        if plan is None:
            continue
        verified.add(name)
        if _local(plan.components[-1], image) and type(module) is ModuleType:
            image.remember_plan(plan)
            image.owned_modules[name] = module
    for name, module in list(image.owned_modules.items()):
        if sys.modules.get(name) is module:
            _verify_ambient_dependencies(
                name, module, image, original_modules=before.modules
            )
    _select_registration_modules(image, before)
    image._registrations = _records(image)
    origins = {
        str(_origin(module))
        for module in (
            *image.owned_modules.values(),
            *image._registration_modules.values(),
        )
    }
    image._relations = {
        key: set(values) for key, values in spec._RELATIONS.items() if key in origins
    }
    image._datatypes = dict(_row._DATATYPE_CACHE)
    image._synthetic_rows = dict(_row._SYNTHETIC_ROW_CACHE)
    image._namespace_states = _namespace_states(image.owned_modules.values())
    return verified


def _restore(before: _Snapshot, image: LayerImage, verified: set[str]) -> None:
    from clauz3 import spec
    from clauz3._vendor.deal_solver._proxies import _row

    changed = {
        name
        for name in set(before.modules) | set(sys.modules)
        if before.modules.get(name, _MISSING) is not sys.modules.get(name, _MISSING)
    }
    restored_parents = {name for name in changed if name in before.modules}
    # Newly imported children belong to the selected package object. If an
    # ancestor is restored to another image, keep those children parked here
    # rather than grafting them (or their registrations) onto the host package.
    verified = {
        name
        for name in verified
        if name in before.modules
        or not any(name.startswith(parent + ".") for parent in restored_parents)
    }
    for name in changed:
        previous = before.modules.get(name, _MISSING)
        if previous is not _MISSING:
            sys.modules[name] = previous
        elif name not in verified:
            sys.modules.pop(name, None)
    links = set(changed)
    for name in set(before.modules) | set(sys.modules):
        parent_name, _, child = name.rpartition(".")
        if parent_name and _namespace(sys.modules.get(parent_name)).get(
            child, _MISSING
        ) is not before.namespaces.get(parent_name, {}).get(child, _MISSING):
            links.add(name)
    for name in links:
        parent_name, _, child = name.rpartition(".")
        if not parent_name:
            continue
        parent = _namespace(sys.modules.get(parent_name))
        if not parent:
            continue
        previous = before.namespaces.get(parent_name, {}).get(child, _MISSING)
        if previous is not _MISSING:
            parent[child] = previous
        elif name not in before.modules and name in sys.modules and name in verified:
            parent[child] = sys.modules[name]
        else:
            parent.pop(child, None)
    _restore_namespace_states(before.namespace_states)
    FUNCTIONS.clear()
    FUNCTIONS.update(before.functions)
    spec.REGISTERED_CONTRACTS.clear()
    spec.REGISTERED_CONTRACTS.update(before.contracts)
    for record in image._registrations:
        if record.name not in before.functions and any(
            sys.modules.get(name) is record.module for name in verified
        ):
            FUNCTIONS[record.name] = record.model
            if record.is_contract:
                spec.REGISTERED_CONTRACTS.add(record.name)
    spec._RELATIONS.clear()
    spec._RELATIONS.update({key: set(value) for key, value in before.relations.items()})
    for key, values in image._relations.items():
        if key not in before.relations:
            spec._RELATIONS[key] = set(values)
    _row._DATATYPE_CACHE.clear()
    _row._DATATYPE_CACHE.update(before.datatypes)
    _row._SYNTHETIC_ROW_CACHE.clear()
    _row._SYNTHETIC_ROW_CACHE.update(before.synthetic_rows)
    sys.path[:] = before.path


def _commit_dependency_history(image: LayerImage) -> None:
    stars: dict[ModuleType, list[tuple[tuple[str, str], _StarBinding]]] = {}
    aliases: dict[ModuleType, list[tuple[tuple[str, str], _AliasBinding]]] = {}
    for key, star in image._star_bindings.items():
        stars.setdefault(star.owner, []).append((key, star))
    for key, alias in image._alias_bindings.items():
        aliases.setdefault(alias.owner, []).append((key, alias))
    image._verified_dependencies = {
        module: _DependencyHistory(
            source, tuple(stars.get(module, ())), tuple(aliases.get(module, ()))
        )
        for module, source in image._dependency_sources.items()
    }


@contextmanager
def _activate(image: LayerImage) -> Iterator[LayerImage]:
    global _ACTIVE
    # Refuse malformed operator settings before initializing selected Python.
    _dependency_limits()
    before = _snapshot()
    verified: set[str] = set()
    primary: BaseException | None = None
    previous_star_bindings = dict(image._star_bindings)
    previous_alias_bindings = dict(image._alias_bindings)
    previous_dependency_sources = dict(image._dependency_sources)
    # Reinstalling a parked identity is not a new initialization, even when
    # another image currently occupies its sys.modules name. Keep strong
    # references taken before capture can add provisional owners.
    image._preexisting_modules = (
        *image.owned_modules.values(),
        *image._registration_modules.values(),
    )
    captured = False
    try:
        sys.path[:] = [str(path) for path in image.paths]
        image._check_fresh()
        _select_python(image)
        _install_registries(image, before)
        image._before = before
        _ACTIVE = image
        from clauz3.layer_astroid import AstroidLayerError, astroid_layer

        try:
            with astroid_layer(image):
                yield image
        except AstroidLayerError as exc:
            raise LayerStateError(str(exc)) from exc
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            if _ACTIVE is image:
                verified = _capture(image, before)
                captured = True
                image._has_verified_state = True
        except Exception as exc:
            if primary is None:
                raise
            primary.add_note(f"layer state capture also failed: {exc}")
        finally:
            if primary is not None or not captured:
                image._star_bindings = previous_star_bindings
                image._alias_bindings = previous_alias_bindings
                image._dependency_sources = previous_dependency_sources
            _ACTIVE = None
            image._before = None
            image._preexisting_modules = ()
            _restore(before, image, verified)
            if primary is None and captured:
                _commit_dependency_history(image)

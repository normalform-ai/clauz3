"""Check import existence without executing a module or its parent packages."""

import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from importlib.machinery import (
    BYTECODE_SUFFIXES,
    EXTENSION_SUFFIXES,
    SOURCE_SUFFIXES,
    BuiltinImporter,
    ExtensionFileLoader,
    FileFinder,
    FrozenImporter,
    ModuleSpec,
    SourceFileLoader,
    SourcelessFileLoader,
)
from pathlib import Path
from threading import get_ident
from types import ModuleType, SimpleNamespace
from typing import Literal

from clauz3.libraries.layout import alternate_tools_module

from .errors import TrustedBoundaryError

# Only pure resolution batches opt in. The cache never spans a call to trusted
# Python, proof contract construction, approval I/O, or entry execution.
_PHASE_FINDERS: ContextVar[
    tuple[int, dict[str, FileFinder], dict[str, Path]] | None
] = ContextVar("clauz3_import_resolution_finders", default=None)


@contextmanager
def resolution_phase() -> Iterator[None]:
    """Reuse finders and absolute path normalization within one pure batch.

    Every lookup still runs the real finder and every authority/cache check is
    unchanged. A new phase creates fresh directory snapshots even if an earlier
    mutation restored directory timestamps. Copied contexts cannot share these
    mutable finder instances with another thread. Resolved paths also expire at
    the phase boundary; import plans and provenance decisions are never cached.
    """
    previous = _PHASE_FINDERS.get()
    if previous is not None and previous[0] == get_ident():
        yield
        return
    token = _PHASE_FINDERS.set((get_ident(), {}, {}))
    try:
        yield
    finally:
        _PHASE_FINDERS.reset(token)


def resolve_path(value: str | Path) -> Path:
    """Normalize an absolute path once per pure phase, without caching metadata.

    Callers still read current module fields and path lists before each call.
    Relative paths are always fresh, including when the current directory
    changes. The normalization memo is discarded before trusted execution and
    each later phase.
    """
    path = Path(value)
    phase = _PHASE_FINDERS.get()
    cache = (
        phase[2]
        if phase is not None and phase[0] == get_ident() and path.is_absolute()
        else None
    )
    key = str(path)
    if cache is not None and key in cache:
        return cache[key]
    resolved = path.resolve()
    if cache is not None:
        cache[key] = resolved
    return resolved


def _file_finder(path: str) -> FileFinder:
    phase = _PHASE_FINDERS.get()
    cache = phase[1] if phase is not None and phase[0] == get_ident() else None
    if cache is not None and path in cache:
        return cache[path]
    finder = FileFinder(
        path,
        (ExtensionFileLoader, EXTENSION_SUFFIXES),
        (SourceFileLoader, SOURCE_SUFFIXES),
        (SourcelessFileLoader, BYTECODE_SUFFIXES),
    )
    if cache is not None:
        cache[path] = finder
    return finder


def loaded_trusted_module(
    name: str,
    *,
    trusted_roots: Iterable[Path],
    import_roots: Iterable[Path],
) -> ModuleType | None:
    """Bind a checked trusted file or an inert trusted namespace container.

    The caller must load file-backed trusted modules in the prover's checked
    trusted-layer context first. Never import a new name selected by the entry:
    a trusted prefix can also name an agent file in another namespace portion.
    ``None`` describes a real namespace directory under a configured root; its
    cached ``__path__`` and attributes are deliberately not consulted.
    """
    roots = tuple(root.resolve() for root in trusted_roots)
    import_root_list = tuple(Path(root).resolve() for root in import_roots)
    try:
        plan = plan_import(name, import_roots=import_root_list)
    except ModuleNotFoundError as exc:
        raise TrustedBoundaryError(
            f"trusted module {name!r} is not loaded from a configured trusted root; "
            "load the checked trusted layer before module validation"
        ) from exc

    for component in plan.components:
        if component.kind != "namespace" and (
            component.origin is None
            or not any(component.origin.is_relative_to(root) for root in roots)
        ):
            raise TrustedBoundaryError(
                f"trusted module {name!r} is not loaded from its configured "
                "trusted root"
            )

    leaf = plan.components[-1]
    if leaf.name != leaf.resolved_name:
        head = name.partition(".")[0]
        for import_root in import_root_list:
            candidate = import_root / head
            if (
                candidate.exists()
                or candidate.is_symlink()
                or (import_root / f"{head}.py").exists()
                or (import_root / f"{head}.py").is_symlink()
            ) and not any(root.is_relative_to(candidate.resolve()) for root in roots):
                raise TrustedBoundaryError(
                    f"trusted module {name!r} is shadowed by {str(candidate)!r} "
                    "outside configured trusted roots"
                )

    if leaf.kind == "namespace":
        if not leaf.locations or not all(
            any(loc.is_relative_to(root) for root in roots) for loc in leaf.locations
        ):
            raise TrustedBoundaryError(
                f"trusted module {name!r} is not loaded from a configured "
                "trusted root; load the checked trusted layer before module "
                "validation"
            )
        if name in sys.modules and type(sys.modules[name]) is not ModuleType:
            raise TrustedBoundaryError(
                f"trusted module {name!r} is not a standard cached module"
            )
        return None

    if leaf.kind != "file" or leaf.origin is None:
        raise TrustedBoundaryError(
            f"trusted module {name!r} has no verified source file"
        )

    module = sys.modules.get(leaf.resolved_name)
    if name in sys.modules:
        direct = sys.modules[name]
        if type(direct) is not ModuleType:
            raise TrustedBoundaryError(
                f"trusted module {name!r} is not a standard cached module"
            )
        if module is not None and direct is not module:
            raise TrustedBoundaryError(
                f"trusted module {name!r} conflicts with cached alias "
                f"{leaf.resolved_name!r}"
            )
        module = direct

    if module is None:
        raise TrustedBoundaryError(
            f"trusted module {name!r} is not loaded from a configured trusted root; "
            "load the checked trusted layer before module validation"
        )
    if type(module) is not ModuleType:
        raise TrustedBoundaryError(
            f"trusted module {name!r} is not a standard cached module"
        )
    module_file = ModuleType.__getattribute__(module, "__dict__").get("__file__")
    if type(module_file) is not str:
        raise TrustedBoundaryError(
            f"trusted module {name!r} has no verified source file"
        )
    if Path(module_file).resolve() != leaf.origin:
        raise TrustedBoundaryError(
            f"trusted module {name!r} is not loaded from its configured trusted root"
        )
    return module


def is_loaded_trusted_module(name: str, trusted_roots: Iterable[Path]) -> bool:
    """Whether a cached module's source is physically inside a trusted root."""
    module = sys.modules.get(name)
    alt = alternate_tools_module(name)
    if alt is not None and alt in sys.modules:
        alt_module = sys.modules[alt]
        if module is not None and module is not alt_module:
            return False
        if module is None:
            module = alt_module
    if type(module) is not ModuleType:
        return False
    module_file = ModuleType.__getattribute__(module, "__dict__").get("__file__")
    if type(module_file) is not str:
        return False
    path = Path(module_file).resolve()
    return any(path.is_relative_to(root.resolve()) for root in trusted_roots)


@dataclass(frozen=True)
class ImportComponent:
    """One selected source, frozen module, or namespace in an import chain."""

    name: str
    resolved_name: str
    kind: Literal["builtin", "frozen", "file", "namespace"]
    origin: Path | None
    locations: tuple[Path, ...] | None


@dataclass(frozen=True)
class ImportPlan:
    """Immutable finder results, without a loaded module or executable loader."""

    name: str
    components: tuple[ImportComponent, ...]


def require_importable(name: str, import_roots: Iterable[Path]) -> None:
    """Check existence only; library authority is a separate validation step."""
    plan_import(name, import_roots=import_roots)


def plan_import(name: str, *, import_roots: Iterable[Path]) -> ImportPlan:
    """Resolve every import component without executing code or cached hooks.

    This only inspects standard finder metadata. It neither calls arbitrary
    import hooks nor reads cached modules. In particular, looking up a dotted
    name must not import its parent to obtain ``__path__``.

    Zip archives and custom loaders are outside this validation subset.
    """
    search_paths = list(
        dict.fromkeys(
            str(resolve_path(path or "."))
            for path in [*(str(root) for root in import_roots), *sys.path]
            if type(path) is str
        )
    )
    # These public names are standard-library aliases. collections.abc has
    # no separate source file on Python 3.14; its package initializer installs
    # the alias. Map existence without executing that initializer or consulting
    # cached modules; callers retain the requested name and its own surface.
    aliases = {
        "os.path": "ntpath" if sys.platform == "win32" else "posixpath",
        "collections.abc": "_collections_abc",
    }
    if any(not part.isidentifier() for part in name.split(".")):
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)
    if name in aliases:
        # The requested parent must be checked even when the child is an alias
        # rather than a physical file. Prefer a real child where one exists
        # (collections.abc is a file on older supported Python versions).
        parent_name = name.rsplit(".", 1)[0]
        parents = _plan_chain(parent_name, search_paths, requested=name)
        locations = parents[-1].locations
        if locations is not None:
            child = _find_spec(name, (str(path) for path in locations))
            if child is not None:
                return ImportPlan(name, (*parents, _component(child)))
        canonical = _plan_chain(aliases[name], search_paths, requested=name)
        alias = replace(canonical[-1], name=name)
        return ImportPlan(name, (*parents, *canonical, alias))
    if (alt := alternate_tools_module(name)) is not None:
        return _plan_tools_import(name, alt, search_paths)
    return ImportPlan(name, _plan_chain(name, search_paths, requested=name))


def _plan_tools_import(name: str, alt: str, search_paths: list[str]) -> ImportPlan:
    head, dot, rest = name.partition(".")
    alt_head = alternate_tools_module(head)
    assert alt_head is not None
    head_spec = _find_spec(head, search_paths)
    alt_head_spec = _find_spec(alt_head, search_paths)
    head_is_regular = head_spec is not None and (
        head_spec.origin is not None or head_spec.loader is not None
    )
    alt_head_is_regular = alt_head_spec is not None and (
        alt_head_spec.origin is not None or alt_head_spec.loader is not None
    )
    if not dot:
        if head_spec is not None:
            return ImportPlan(name, _plan_chain(name, search_paths, requested=name))
        if alt_head_is_regular:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        canonical = _plan_chain(alt, search_paths, requested=name)
        alias = replace(canonical[-1], name=name)
        return ImportPlan(name, (*canonical, alias))

    top = rest.partition(".")[0]
    primary_top = (
        _find_spec(
            f"{head}.{top}",
            (str(p) for p in head_spec.submodule_search_locations),
        )
        if head_spec is not None and head_spec.submodule_search_locations is not None
        else None
    )
    alt_top = (
        _find_spec(
            f"{alt_head}.{top}",
            (str(p) for p in alt_head_spec.submodule_search_locations),
        )
        if alt_head_spec is not None
        and alt_head_spec.submodule_search_locations is not None
        else None
    )
    if primary_top is not None and alt_top is not None:
        raise ModuleNotFoundError(
            f"Ambiguous module {name!r}: {top!r} is provided under both "
            f"{head!r} and {alt_head!r}",
            name=name,
        )
    if primary_top is not None:
        return ImportPlan(name, _plan_chain(name, search_paths, requested=name))
    if alt_top is not None and not head_is_regular and not alt_head_is_regular:
        canonical = _plan_chain(alt, search_paths, requested=name)
        alias = replace(canonical[-1], name=name)
        return ImportPlan(name, (*canonical, alias))
    raise ModuleNotFoundError(f"No module named {name!r}", name=name)


def _plan_chain(
    name: str, search_paths: list[str], *, requested: str
) -> tuple[ImportComponent, ...]:
    parts = name.split(".")
    components: list[ImportComponent] = []
    for index in range(len(parts)):
        current = ".".join(parts[: index + 1])
        spec = _find_spec(current, search_paths)
        if spec is None:
            raise ModuleNotFoundError(f"No module named {requested!r}", name=requested)
        component = _component(spec)
        components.append(component)
        if index == len(parts) - 1:
            return tuple(components)
        locations = component.locations
        if locations is None:
            raise ModuleNotFoundError(
                f"No module named {requested!r}; {current!r} is not a package",
                name=requested,
            )
        search_paths = [str(path) for path in locations]
    raise AssertionError("empty import name")  # pragma: no cover


def _component(spec: ModuleSpec) -> ImportComponent:
    locations = spec.submodule_search_locations
    paths = (
        tuple(dict.fromkeys(resolve_path(path) for path in locations))
        if locations is not None
        else None
    )
    if spec.origin == "built-in":
        kind: Literal["builtin", "frozen", "file", "namespace"] = "builtin"
        origin = None
    elif spec.origin == "frozen":
        kind = "frozen"
        # Frozen stdlib modules can still publish their canonical source file.
        # This comes from the fresh standard finder, never a cached module.
        state = spec.loader_state
        filename = (
            vars(state).get("filename") if type(state) is SimpleNamespace else None
        )
        origin = resolve_path(filename) if type(filename) is str else None
    elif spec.origin is None:
        kind, origin = "namespace", None
    else:
        kind, origin = "file", resolve_path(spec.origin)
    return ImportComponent(spec.name, spec.name, kind, origin, paths)


def _find_spec(name: str, search_paths: Iterable[str]) -> ModuleSpec | None:
    for builtin_finder in (BuiltinImporter, FrozenImporter):
        spec = builtin_finder.find_spec(name)
        if spec is not None:
            return spec

    namespace_paths: list[str] = []
    for path in search_paths:
        finder = _file_finder(path or str(Path.cwd()))
        spec = finder.find_spec(name)
        if spec is None:
            continue
        if spec.loader is not None:
            # A regular package/module overrides any earlier namespace
            # portions, and prevents searching later roots for its children.
            return spec
        # FileFinder returns a plain list here. PathFinder would wrap the
        # combined paths in _NamespacePath, whose iteration can consult the
        # live parent's attributes in sys.modules.
        namespace_paths.extend(spec.submodule_search_locations or ())
    if not namespace_paths:
        return None
    spec = ModuleSpec(name, loader=None, is_package=True)
    spec.submodule_search_locations = namespace_paths
    return spec

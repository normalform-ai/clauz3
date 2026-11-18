"""Read policy call targets from configured roots without importing modules."""

from __future__ import annotations

import ast
import tokenize
from collections.abc import Iterable
from pathlib import Path

import astroid
from astroid import nodes
from astroid.builder import AstroidBuilder
from astroid.manager import AstroidManager

from clauz3 import decorators
from clauz3.libraries.layout import alternate_tools_module


def parse_source(
    source: str, name: str = "", path: str | None = None
) -> nodes.Module | None:
    """Disable Astroid's import/inference postprocessing and shared cache writes.

    Even with transforms disabled, Astroid resolves star imports and infers
    attribute assignment receivers while building a tree. Refuse those source
    shapes here, before giving them to its builder.
    """
    tree = ast.parse(source)
    if any(
        (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "*" for alias in node.names)
        )
        or (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, (ast.Store, ast.Del))
        )
        for node in ast.walk(tree)
    ):
        return None
    manager = AstroidManager()
    # Astroid 4 gives each manager its own attributes while initially sharing
    # the cache object. Rebinding this instance isolates our builder; it does
    # not replace MANAGER's cache or AstroidManager.brain["astroid_cache"].
    manager.astroid_cache = {}
    return AstroidBuilder(manager, apply_transforms=False).string_build(
        source, name, path
    )


class SourceModules:
    """A per-analysis source cache, independent of Python's import caches."""

    def __init__(
        self, import_roots: Iterable[Path], trusted_roots: Iterable[Path]
    ) -> None:
        self.import_roots = tuple(Path(root).resolve() for root in import_roots)
        self.trusted_roots = tuple(Path(root).resolve() for root in trusted_roots)
        self.modules: dict[str, nodes.Module | None] = {}
        self.locations: dict[str, tuple[Path, ...]] = {}

    def load(self, name: str) -> nodes.Module | None:
        if name in self.modules:
            return self.modules[name]
        alt = alternate_tools_module(name)
        if alt is not None:
            mode = self._tools_resolution_mode(name, alt)
            if mode == "reject":
                self.modules[name] = None
                return None
            if mode == "primary":
                return self._load_exact(name)
            module = self._load_exact(alt, public_name=name)
            self.modules[name] = module
            if alt in self.locations:
                self.locations[name] = self.locations[alt]
            return module
        return self._load_exact(name)

    def _tools_resolution_mode(self, name: str, alt: str) -> str:
        head, dot, rest = name.partition(".")
        alt_head = alternate_tools_module(head)
        if alt_head is None:
            return "primary"
        head_regular = any(
            (root / head / "__init__.py").exists() or (root / f"{head}.py").exists()
            for root in self.import_roots
        )
        alt_regular = any(
            (root / alt_head / "__init__.py").exists()
            or (root / f"{alt_head}.py").exists()
            for root in self.import_roots
        )
        if not dot:
            if (
                any((root / head).exists() for root in self.import_roots)
                or head_regular
            ):
                return "primary"
            if alt_regular:
                return "reject"
            return "alt"
        top = rest.partition(".")[0]
        has_primary = any(
            (root / head / top).exists()
            or (root / head / top).is_symlink()
            or (root / head / f"{top}.py").exists()
            or (root / head / f"{top}.py").is_symlink()
            for root in self.import_roots
        )
        has_alt = any(
            (root / alt_head / top).exists()
            or (root / alt_head / top).is_symlink()
            or (root / alt_head / f"{top}.py").exists()
            or (root / alt_head / f"{top}.py").is_symlink()
            for root in self.import_roots
        )
        if has_primary and has_alt:
            return "reject"
        if has_primary:
            return "primary"
        if has_alt and not head_regular and not alt_regular:
            for root in self.import_roots:
                candidate = root / head
                if (candidate.exists() or candidate.is_symlink()) and not any(
                    trusted.is_relative_to(candidate.resolve())
                    for trusted in self.trusted_roots
                ):
                    return "reject"
            return "alt"
        return "reject"

    def _load_exact(
        self, name: str, *, public_name: str | None = None
    ) -> nodes.Module | None:
        if name in self.modules:
            return self.modules[name]
        self.modules[name] = None
        if not name or not all(part.isidentifier() for part in name.split(".")):
            return None
        ast_name = public_name or name
        parent_name, _, leaf = name.rpartition(".")
        if parent_name:
            parent = self.load(parent_name)
            if parent is None or not parent.package:
                return None
            locations = self.locations[parent_name]
        else:
            locations = self.import_roots
        namespaces: list[Path] = []
        for location in locations:
            directory = location / leaf
            # A regular package or module wins over namespace portions. Once
            # selected, a regular package restricts all child lookups to its
            # own directory, even when another import root has that child.
            for candidate in (directory / "__init__.py", location / f"{leaf}.py"):
                if not candidate.is_file():
                    continue
                path = candidate.resolve()
                if not self._within_roots(path):
                    return None
                try:
                    with tokenize.open(path) as source_file:
                        module = parse_source(source_file.read(), ast_name, str(path))
                except (
                    OSError,
                    UnicodeError,
                    SyntaxError,
                    ValueError,
                    RecursionError,
                    astroid.AstroidError,
                ):
                    return None
                self.modules[name] = module
                if module is not None and module.package:
                    self.locations[name] = (path.parent,)
                return module
            if directory.is_dir():
                resolved = directory.resolve()
                if not self._within_roots(resolved):
                    return None
                namespaces.append(resolved)
        if namespaces:
            module = parse_source("", ast_name)
            assert module is not None
            module.package = True
            self.modules[name] = module
            self.locations[name] = tuple(namespaces)
            return module
        return None

    def _within_roots(self, path: Path) -> bool:
        return any(path.is_relative_to(root) for root in self.import_roots)

    @staticmethod
    def imported_module(binding: nodes.ImportFrom) -> str:
        if not binding.level:
            return binding.modname or ""
        module = binding.root()
        parts = module.name.split(".")
        if not module.package:
            parts.pop()
        if binding.level > len(parts):
            return ""
        prefix = parts[: len(parts) - binding.level + 1]
        return ".".join([*prefix, *filter(None, (binding.modname,))])

    def is_boundary(self, function: nodes.FunctionDef) -> bool:
        module = function.root()
        if not module.file or not any(
            Path(module.file).resolve().is_relative_to(root)
            for root in self.trusted_roots
        ):
            return False
        return decorators.is_trusted_effect(function, decorators.import_aliases(module))

"""Allowlist policy for imports in untrusted agent-authored source.

Trusted code (under ``trusted_roots``) is free to import anything it needs to
implement effects. Untrusted entry files — the program the agent submits to
``clauz3 prove`` or ``clauz3 run`` — are restricted: they may only import the
``clauz3`` framework, the trusted layers reachable from the configured roots,
a small set of pure/functional stdlib modules, and any extras the operator
explicitly opts into.

This module is the single source of truth for that allowlist. Both the prove
path and the run path call into it, so an agent cannot reach the executor by
slipping past one and into the other.
"""

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from clauz3.libraries.layout import alternate_tools_module

#: Always allowed: the clauz3 framework, deal (its decorators are gated
#: separately in :func:`clauz3.prover.validate_untrusted_source`), and the
#: ``__future__`` pragma module.
ALWAYS_ALLOWED_ROOTS: frozenset[str] = frozenset({"clauz3", "deal", "__future__"})

#: Pure / functional stdlib modules with no I/O, no time access, and no
#: non-determinism. Adding to this list is a security decision — keep it
#: tight and prefer per-project opt-in via ``extra_allowed_imports``.
DEFAULT_STDLIB_ALLOWED: frozenset[str] = frozenset(
    {
        "collections",
        "dataclasses",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "itertools",
        "json",
        "math",
        "re",
        "statistics",
        "string",
        "typing",
    }
)
# Note: ``operator`` is deliberately NOT allowed by default — its
# ``attrgetter`` / ``methodcaller`` accept dunder attribute names as string
# literals, which slip past the runtime AST attribute denylist and reach
# ``__subclasses__`` / ``__globals__`` (see issue #68). Opt in per project with
# ``extra_allowed_imports`` only if a trusted layer genuinely needs it.


@dataclass(frozen=True)
class ImportPolicy:
    """Resolved set of importable module prefixes for untrusted source."""

    #: Top-level module roots allowed (``import X`` or ``from X.y import z``
    #: succeeds iff ``X`` is in here).
    allowed_roots: frozenset[str]
    #: Dotted module prefixes that are also allowed even though their root
    #: isn't in :attr:`allowed_roots`. Used for ``tools.<domain>.trusted``
    #: paths where we want ``tools.email.trusted`` but not ``tools.email``
    #: or ``tools`` as a whole.
    allowed_prefixes: tuple[str, ...]
    #: The subset of :attr:`allowed_prefixes` derived from ``trusted_roots``.
    #:
    #: Importability and trust are different questions and only overlap by
    #: default. A dotted ``extra_allowed`` entry (the ``--allow-import`` opt-in)
    #: lands in :attr:`allowed_prefixes` so the module can be imported, but it
    #: lives outside every trusted root and declares no effects, so
    #: it must not be treated as a trusted layer. Consumers deciding *trust*
    #: rather than *importability* must read this field.
    trusted_prefixes: tuple[str, ...] = ()
    #: Physical trust boundary. A matching dotted prefix alone does not make
    #: a module trusted: namespace packages can acquire agent-owned portions.
    trusted_roots: tuple[Path, ...] = ()

    def is_allowed(self, module: str) -> bool:
        root = module.partition(".")[0]
        if root in self.allowed_roots:
            return True
        for prefix in self.allowed_prefixes:
            if module == prefix or module.startswith(prefix + "."):
                return True
        return False


class DisallowedImportError(Exception):
    """An untrusted source imported a module outside the allowlist."""


def build_policy(
    *,
    trusted_roots: Iterable[Path] = (),
    import_roots: Iterable[Path] = (),
    extra_allowed: Iterable[str] = (),
) -> ImportPolicy:
    """Compose the default allowlist with trusted-derived prefixes and extras."""
    allowed_roots = set(ALWAYS_ALLOWED_ROOTS) | set(DEFAULT_STDLIB_ALLOWED)
    extras = list(extra_allowed)
    trusted_root_list = tuple(Path(root).resolve() for root in trusted_roots)
    trusted_prefixes = _derive_trusted_prefixes(
        trusted_roots=trusted_root_list,
        import_roots=import_roots,
    )
    # An extra that names a single top-level module is treated as a root; one
    # that contains a dot is treated as a dotted prefix so an operator can
    # opt into, e.g., ``tools.legacy.helpers`` without unlocking all of
    # ``tools``.
    prefix_extras: list[str] = []
    for entry in extras:
        if "." in entry:
            prefix_extras.append(entry)
        else:
            allowed_roots.add(entry)
    return ImportPolicy(
        allowed_roots=frozenset(allowed_roots),
        allowed_prefixes=tuple(sorted(set(trusted_prefixes) | set(prefix_extras))),
        trusted_prefixes=tuple(sorted(set(trusted_prefixes))),
        trusted_roots=trusted_root_list,
    )


def _derive_trusted_prefixes(
    *,
    trusted_roots: Iterable[Path],
    import_roots: Iterable[Path],
) -> list[str]:
    import_paths = [Path(p).resolve() for p in import_roots]
    resolved_roots = [Path(root).resolve() for root in trusted_roots]
    prefixes: list[str] = []
    for resolved in resolved_roots:
        for import_path in import_paths:
            try:
                relative = resolved.relative_to(import_path)
            except ValueError:
                continue
            parts = relative.parts
            if not parts or not all(part.isidentifier() for part in parts):
                continue
            prefix = ".".join(parts)
            prefixes.append(prefix)
            if (alt := alternate_tools_module(prefix)) and _can_alias_trusted_prefix(
                parts=parts,
                import_paths=import_paths,
                trusted_roots=resolved_roots,
            ):
                prefixes.append(alt)
            break
    return prefixes


def _can_alias_trusted_prefix(
    *,
    parts: tuple[str, ...],
    import_paths: list[Path],
    trusted_roots: list[Path],
) -> bool:
    if len(parts) < 2:
        return False
    head, domain_top = parts[0], parts[1]
    alt_head = alternate_tools_module(head)
    if alt_head is None:
        return False
    for import_path in import_paths:
        for pkg in (head, alt_head):
            if (import_path / pkg / "__init__.py").exists() or (
                import_path / f"{pkg}.py"
            ).exists():
                return False
        alt_dir = import_path / alt_head
        if (
            (alt_dir / domain_top).exists()
            or (alt_dir / domain_top).is_symlink()
            or (alt_dir / f"{domain_top}.py").exists()
            or (alt_dir / f"{domain_top}.py").is_symlink()
        ):
            return False
        if (alt_dir.exists() or alt_dir.is_symlink()) and not any(
            root.is_relative_to(alt_dir.resolve()) for root in trusted_roots
        ):
            return False
    return True


def validate_imports(
    *,
    source: str,
    source_name: str,
    policy: ImportPolicy,
) -> None:
    """Walk ``source`` and raise on any disallowed ``import``/``from`` target."""
    module = ast.parse(source, filename=source_name)
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check(alias.name, policy=policy, source_name=source_name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and not node.module:
                raise DisallowedImportError(
                    f"{source_name}: relative imports are not allowed in "
                    "untrusted source",
                )
            if node.module is not None:
                _check(node.module, policy=policy, source_name=source_name)


def _check(module: str, *, policy: ImportPolicy, source_name: str) -> None:
    if not policy.is_allowed(module):
        raise DisallowedImportError(
            f"{source_name}: import of {module!r} is not in the allowlist "
            "(use a trusted effect or pass --allow-import to opt in)",
        )

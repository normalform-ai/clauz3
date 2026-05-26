"""Domain coverage policies for trusted layers.

A ``DomainPolicy`` lets a Trusted Layer Engineer state, per domain:

- which trusted effect activates the domain (``when_used``)
- which contracts an agent SHOULD state about it (``recommended``)
- which it MUST prove (``required``) — enforcement is a follow-up slice

Policies are consumed as data only. The recommended/silent checks are flat set
operations over the program AST and the agent's guarantee expressions: no agent
code runs and no solver work is added.
"""

from __future__ import annotations

import ast
import importlib
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class DomainPolicy:
    when_used: tuple[str, ...]
    recommended: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    label: str | None = None
    # filled by the loader, e.g. "tools.email.trusted"
    domain_package: str | None = None


def domain_policy(
    *,
    when_used: str | Sequence[str],
    recommended: Sequence[str] = (),
    required: Sequence[str] = (),
    label: str | None = None,
) -> DomainPolicy:
    """Author a domain policy in a trusted-root ``policy.py`` manifest."""

    triggers = (when_used,) if isinstance(when_used, str) else tuple(when_used)
    return DomainPolicy(
        when_used=triggers,
        recommended=tuple(recommended),
        required=tuple(required),
        label=label,
    )


def load_domain_policies(
    *,
    trusted_roots: Iterable[Path],
    import_roots: Iterable[Path],
) -> list[DomainPolicy]:
    """Load ``policy.py`` manifests from trusted roots.

    Each trusted root may contain a ``policy.py`` exposing a module-level
    ``POLICY`` that is a ``DomainPolicy`` or a sequence of them. The loader
    stamps each policy with the dotted package of its trusted root so coverage
    can map an agent's guarantees back to the domain they constrain.
    """

    import_paths = [Path(p).resolve() for p in import_roots]
    policies: list[DomainPolicy] = []
    sys.path[:0] = [str(p) for p in import_paths]
    try:
        importlib.invalidate_caches()
        for root in trusted_roots:
            policy_file = Path(root).resolve() / "policy.py"
            if not policy_file.is_file():
                continue
            package = _dotted_name(policy_file.parent, import_paths)
            module = importlib.import_module(
                f"{package}.policy" if package else "policy",
            )
            raw = getattr(module, "POLICY", ())
            found = (raw,) if isinstance(raw, DomainPolicy) else tuple(raw)
            policies += [replace(p, domain_package=package) for p in found]
    finally:
        del sys.path[: len(import_paths)]
    return policies


def compute_coverage(
    *,
    source: str,
    guarantees: Sequence[str],
    policies: Sequence[DomainPolicy],
) -> list[dict[str, object]]:
    """Report, per used domain, whether the agent's guarantees cover it.

    ``source`` is the agent program; ``guarantees`` are the unparsed guarantee
    expressions (as produced for the approval request). Statuses:

    - ``covered`` — domain used, all recommended/required contracts stated
    - ``recommended_gap`` — domain addressed but a recommended contract missing
    - ``silent_gap`` — domain used but the agent stated nothing about it
    - ``required_gap`` — a required contract missing (surfaced now; rejected
      once required-enforcement lands in the prover)

    "Used" is an AST over-approximation: a trusted call in a dead branch still
    counts. For a warning that is the safe direction.
    """

    called = _called_names(source)
    aliases = _alias_modules(source)

    addressed_names: set[str] = set()
    addressed_packages: set[str] = set()
    for guarantee in guarantees:
        callee = _callee(ast.parse(guarantee, mode="eval").body)
        if callee is None:
            continue
        addressed_names.add(callee.name)
        module = aliases.get(callee.base)
        if module is not None:
            addressed_packages.add(module)

    report: list[dict[str, object]] = []
    for policy in policies:
        if not (called & set(policy.when_used)):
            continue

        in_domain = policy.domain_package is not None and any(
            module == policy.domain_package
            or module.startswith(policy.domain_package + ".")
            for module in addressed_packages
        )
        missing_required = [c for c in policy.required if c not in addressed_names]
        missing_recommended = [
            c for c in policy.recommended if c not in addressed_names
        ]

        if missing_required:
            status = "required_gap"
        elif not in_domain:
            status = "silent_gap"
        elif missing_recommended:
            status = "recommended_gap"
        else:
            status = "covered"

        report.append(
            {
                "domain": policy.label or " / ".join(policy.when_used),
                "status": status,
                "missing_required": missing_required,
                "missing_recommended": missing_recommended,
            }
        )
    return report


def calls_in(node: ast.AST) -> set[str]:
    """Trailing names of every call within an AST node (over-approximation)."""

    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            callee = _callee(child)
            if callee is not None:
                names.add(callee.name)
    return names


@dataclass(frozen=True)
class _Callee:
    base: str  # leading name: "emails" in emails.only(...); "" for a bare call
    name: str  # trailing name: "only", or "send_email"


def _callee(node: ast.expr) -> _Callee | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return _Callee(base="", name=func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return _Callee(base=func.value.id, name=func.attr)
    return None


def _called_names(source: str) -> set[str]:
    return calls_in(ast.parse(source))


def _alias_modules(source: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name
    return aliases


def _dotted_name(path: Path, import_paths: Sequence[Path]) -> str:
    path = path.resolve()
    for base in import_paths:
        try:
            parts = path.relative_to(base).parts
        except ValueError:
            continue
        if all(part.isidentifier() for part in parts):
            return ".".join(parts)
    return ""

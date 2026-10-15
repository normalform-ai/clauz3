"""Domain coverage policies for trusted layers.

A ``DomainPolicy`` lets a Trusted Layer Engineer state, per domain:

- which trusted effect activates the domain (``when_used``)
- which contracts an agent SHOULD state about it (``recommended``)
- which it MUST prove (``required``)

Policies are consumed as data only. Coverage resolves source bindings and
compares the agent's guarantee expressions: no agent code runs. Required
contracts are separately conjoined into the proof by the prover.
"""

from __future__ import annotations

import ast
import importlib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from clauz3 import decorators as decorators_mod
from clauz3.policy_calls import DYNAMIC_CALL
from clauz3.policy_calls import transitive_calls as transitive_calls


class DuplicateEffectError(Exception):
    """Two trusted effects under the proof's roots share a name.

    Addressing is by name now --- a relation's, a ``when_used``'s --- so one
    name meaning two functions is a configuration a contract cannot express.
    Refused rather than resolved to whichever file was read last, which is what
    a dict keyed on the name alone would silently do.
    """


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

    # Import lazily: prover uses this module's source-only effect scanner
    # while establishing the checked layer context.
    from clauz3.prover import _trusted_layer_context

    roots = [Path(root) for root in trusted_roots]
    import_paths = [Path(path).resolve() for path in import_roots]
    with _trusted_layer_context(roots, import_paths):
        return _load_domain_policy_manifests(roots, import_paths)


def _load_domain_policy_manifests(
    trusted_roots: Sequence[Path], import_paths: Sequence[Path]
) -> list[DomainPolicy]:
    """Read manifests after their imports passed the checked layer loader."""
    policies: list[DomainPolicy] = []
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
    return policies


def compute_coverage(
    *,
    source: str,
    guarantees: Sequence[str],
    policies: Sequence[DomainPolicy],
    target: str = "main",
    import_roots: Iterable[Path] = (),
    trusted_roots: Iterable[Path] = (),
) -> list[dict[str, object]]:
    """Report, per used domain, whether the agent's guarantees cover it.

    ``source`` is the agent program; ``guarantees`` are the unparsed guarantee
    expressions (as produced for the approval request). Statuses:

    - ``covered`` — domain used, all recommended/required contracts stated
    - ``recommended_gap`` — domain addressed but a recommended contract missing
    - ``silent_gap`` — domain used but the agent stated nothing about it
    - ``required_gap`` — a required contract not stated explicitly (the prover
      still enforces it)

    "Used" follows the selected entry and its called helpers. A trusted call
    in a dead branch still counts, but an unused function does not.
    Supply import/trusted roots to resolve imported helpers and boundaries;
    unresolved imports conservatively activate every policy.

    Raises ``ValueError`` when ``target`` does not name a function defined
    directly at module level. Pass an explicit target for a non-``main`` entry.
    """

    tree = ast.parse(source)
    if not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == target
        for node in tree.body
    ):
        raise ValueError(
            f"coverage target {target!r} was not found as a module-level function; "
            "pass target='<entry function>' for the entry you intend to analyze"
        )
    called = transitive_calls(
        source, target, import_roots=import_roots, trusted_roots=trusted_roots
    )
    # Entry decorators resolve module bindings, never imports in an unused
    # function or class. Share that resolution with guarantee validation.
    aliases = decorators_mod.import_aliases(tree)

    addressed_names: set[str] = set()
    addressed_packages: set[str] = set()
    for guarantee in guarantees:
        expression = ast.parse(guarantee, mode="eval").body
        if not isinstance(expression, ast.Call):
            continue
        callee = decorators_mod.canonical_target(
            decorators_mod.dotted_name(expression), aliases
        )
        module, _, name = callee.rpartition(".")
        addressed_names.add(name)
        if module:
            addressed_packages.add(module)

    report: list[dict[str, object]] = []
    for policy in policies:
        if not domain_triggered(called, policy.when_used):
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
    """Trailing names of every call within an AST node (over-approximation).

    This public syntactic helper includes nested scopes and does not resolve
    aliases. Policy triggering uses :func:`transitive_calls` instead.
    """

    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            callee = _callee(child)
            if callee is not None:
                names.add(callee.name)
    return names


def load_trusted_effects(
    trusted_roots: Iterable[Path],
) -> dict[str, tuple[str, ...]]:
    """Every trusted effect under these roots, as ``{name: parameter names}``.

    The names are what a ``when_used`` may name and what a relation may
    address; the parameters are the fields a contract may read off a recorded
    call. Both are read from source rather than from the loaded modules,
    because a policy is checked before anything is proved.

    A ``when_used`` could once also name a ``@deal.has`` marker, resolving to
    whichever functions carried that string. Effects address each other by name
    now (see :mod:`clauz3.effects`), so a marker is no longer a token this
    knows --- and an unknown token is refused rather than silently matching
    nothing, which is what :func:`clauz3.prover._validate_required_triggers`
    exists to do.
    """

    effects: dict[str, tuple[str, ...]] = {}
    origins: dict[str, str] = {}
    for node, _, path in _effect_definitions(trusted_roots):
        if node.name in effects:
            raise DuplicateEffectError(
                f"two trusted effects are called {node.name!r}: "
                f"{origins[node.name]} and {path}. A name is how a relation and "
                "a when_used address an effect, so proving these layers "
                "together would give one name two meanings. Rename one, or "
                "prove them separately.",
            )
        effects[node.name] = _parameter_names(node)
        origins[node.name] = str(path)
    return effects


def load_effect_markers(
    trusted_roots: Iterable[Path],
) -> dict[str, tuple[str, ...]]:
    """Which effects carry each legacy ``@deal.has`` marker, ``{marker: names}``.

    Markers no longer address anything --- neither a relation nor a
    ``when_used`` --- so this exists only to *refuse* a policy whose trigger
    token reads like one. A token that was a marker used to reach every effect
    carrying it; the same word now reaches at most the one effect of that name,
    and a required obligation that quietly stops covering the rest is the one
    way this migration can fail open. See
    :func:`clauz3.prover._validate_required_triggers`.
    """

    markers: dict[str, list[str]] = {}
    for node, aliases, _ in _effect_definitions(trusted_roots):
        for decorator in _decorators_of(node):
            for declared in decorators_mod.unfold_chain(decorator, aliases):
                if not decorators_mod.is_deal_has(declared, aliases):
                    continue
                for marker in decorators_mod.deal_markers(declared):
                    markers.setdefault(marker, []).append(node.name)
    return {marker: tuple(names) for marker, names in markers.items()}


def _effect_definitions(
    trusted_roots: Iterable[Path],
) -> Iterator[tuple[ast.FunctionDef, dict[str, str], Path]]:
    """Every trusted effect under these roots, with its aliases and its file.

    Each file is read once however many roots reach it. Roots repeat and nest
    --- ``--trusted-root tools --trusted-root tools/email/trusted`` is a
    reasonable thing to write --- and a second visit to one definition is not
    two effects sharing a name, which is what :func:`load_trusted_effects`
    refuses.
    """

    seen: set[Path] = set()
    for root in trusted_roots:
        for path in sorted(Path(root).rglob("*.py")):
            if path.name == "__init__.py":
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            module = ast.parse(path.read_text(), filename=str(path))
            aliases = decorators_mod.import_aliases(module)
            for node in module.body:
                if not isinstance(node, ast.FunctionDef):
                    continue
                if decorators_mod.is_trusted_effect(node, aliases):
                    yield node, aliases, path


def _decorators_of(node: ast.FunctionDef) -> list[ast.expr]:
    return list(node.decorator_list)


def _parameter_names(node: ast.FunctionDef) -> tuple[str, ...]:
    """Every parameter a call can bind, in signature order.

    Keyword-only parameters count: ``send(to, *, dry_run=True)`` is the
    dry-run safety pattern, and ``dry_run`` is exactly the field its contract
    reads. (A parameter left to its default is not *recorded* on the fact, so
    a contract reading it still fails closed at solve time -- see
    ``tests/test_dry_run_kwarg.py``. This set is what a contract may name, not
    what every call carries.)
    """

    args = node.args
    return tuple(arg.arg for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs])


def domain_triggered(used: set[str], when_used: Sequence[str]) -> bool:
    """True if a trigger is reached or a dynamic call cannot rule it out."""

    return DYNAMIC_CALL in used or bool(used & set(when_used))


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

"""Approval policies for the clauz3 approval service.

A *policy admin* — the user acting in a config role, distinct from the Trusted
Layer Engineer who writes the trusted layer — authors these rules. They let the
approval service decide a request automatically (``auto_approved`` or
``auto_rejected``) instead of always waiting for a human.

Rules are evaluated by **entailment**, not by inspecting contract text. A clause
is a contract expression (``pol.only(["bob@example.com"])``); the service asks
the prover whether the program *entails* it by conjoining the clause as an extra
``@clauz3.guarantee(...)`` and re-proving the target. This is the same
conjoin-and-prove mechanism the ``required`` coverage tier uses, so a clause
holds whenever it is logically implied by what the program proves — even if the
program never mentions that contract by name.

The two decisions are duals over the same primitive:

- ``auto_approved`` fires when the program **entails all** ``when_proven``
  clauses. An allow-list alone is unsafe (one whitelisted address emailed ten
  thousand times), so the admin makes the conjunction complete by also requiring
  the count/uniqueness contracts that bound it.
- ``auto_rejected`` fires when the program does **not** entail every
  ``unless_proven`` clause — "reject unless you have proven you avoid this." A
  block-list entry ``pol.recipient_at_most("ceo@example.com", 0)`` rejects any
  program that cannot prove the CEO is never emailed, including one that reaches
  a data-dependent recipient under weak guarantees.
"""

from __future__ import annotations

import ast
import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from clauz3.approval import APPROVED_DECISIONS
from clauz3.prover import prove_text

AUTO_DECISIONS = frozenset({"auto_approved", "auto_rejected"})
_CLAUSE_KEY = {"auto_approved": "when_proven", "auto_rejected": "unless_proven"}

# prove_text mutates global sys.path and the import system, so entailment checks
# must not run concurrently within one process.
_PROVE_LOCK = threading.Lock()


class ApprovalPolicyError(Exception):
    """The approval policy file is missing required fields or malformed."""


@dataclass(frozen=True)
class Rule:
    name: str
    decision: str  # one of AUTO_DECISIONS
    clauses: tuple[str, ...]  # contract expressions to test for entailment
    reason: str | None = None


@dataclass(frozen=True)
class ApprovalPolicy:
    # (alias, dotted module) pairs the clause expressions resolve through
    imports: tuple[tuple[str, str], ...]
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class PolicyDecision:
    decision: str
    rule: str
    reason: str | None


def load_approval_policy(path: Path) -> ApprovalPolicy:
    """Load an approval policy from a JSON file authored by the policy admin."""

    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ApprovalPolicyError(f"approval policy not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ApprovalPolicyError(f"invalid approval policy {path}: {exc}") from exc
    return approval_policy_from_dict(raw)


def approval_policy_from_dict(data: object) -> ApprovalPolicy:
    """Build an ``ApprovalPolicy`` from already-parsed JSON data."""

    if not isinstance(data, dict):
        raise ApprovalPolicyError("approval policy must be a JSON object")
    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ApprovalPolicyError("approval policy 'rules' must be a list")
    return ApprovalPolicy(
        imports=_imports_from_dict(data.get("imports", {})),
        rules=tuple(_rule_from_dict(item) for item in raw_rules),
    )


def evaluate_policy(
    policy: ApprovalPolicy,
    request: object,
) -> PolicyDecision | None:
    """Return an auto-decision for a request, or ``None`` to ask a human.

    Deny wins: every ``auto_rejected`` rule is evaluated before any
    ``auto_approved`` rule, so an allow-list cannot override a block-list. A
    clause whose entailment cannot be evaluated (a bad expression, a prover
    error) is skipped, so the request falls through to a human rather than being
    auto-decided on incomplete information.
    """

    if not isinstance(request, dict):
        return None
    program = request.get("program")
    if not isinstance(program, str) or not program:
        return None
    raw_target = request.get("target")
    target = raw_target if isinstance(raw_target, str) and raw_target else "main"
    trusted_roots = _path_list(request.get("trusted_roots"))
    import_roots = _path_list(request.get("import_roots"))

    for wanted in ("auto_rejected", "auto_approved"):
        for rule in policy.rules:
            if rule.decision != wanted:
                continue
            entailed = _entails_all(
                program=program,
                clauses=rule.clauses,
                imports=policy.imports,
                target=target,
                trusted_roots=trusted_roots,
                import_roots=import_roots,
            )
            if entailed is None:
                continue
            fires = entailed if rule.decision == "auto_approved" else not entailed
            if fires:
                return PolicyDecision(
                    decision=rule.decision,
                    rule=rule.name,
                    reason=rule.reason,
                )
    return None


def _entails_all(
    *,
    program: str,
    clauses: tuple[str, ...],
    imports: tuple[tuple[str, str], ...],
    target: str,
    trusted_roots: list[Path],
    import_roots: list[Path],
) -> bool | None:
    """Whether ``program`` entails every clause, or ``None`` if undeterminable.

    Entailment of a conjunction is entailment of each conjunct, so all clauses
    are conjoined and the target is proved once.
    """

    if not clauses:
        return True
    try:
        augmented = _conjoin(
            program,
            clauses=clauses,
            imports=imports,
            target=target,
        )
        with _PROVE_LOCK:
            proofs = prove_text(
                augmented,
                target=target,
                import_paths=import_roots,
                trusted_roots=trusted_roots,
                source_name="<approval-policy>",
            )
    except Exception as exc:
        print(
            f"approval policy: could not evaluate {clauses}: {exc}",
            file=sys.stderr,
        )
        return None
    if not proofs:
        return None
    return all(result.ok for result in proofs)


def _conjoin(
    program: str,
    *,
    clauses: tuple[str, ...],
    imports: tuple[tuple[str, str], ...],
    target: str,
) -> str:
    """Add ``@clauz3.guarantee(<clause>)`` decorators to the target function.

    Policy aliases are rewritten to collision-proof internal names so a clause
    cannot be captured by a same-named binding in the agent's program.
    """

    tree = ast.parse(program)
    alias_map = {
        alias: f"_clauz3_pol_{index}" for index, (alias, _) in enumerate(imports)
    }
    rewriter = _AliasRewriter(alias_map)

    found = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == target:
            for clause in clauses:
                expr = ast.parse(clause, mode="eval").body
                node.decorator_list.insert(0, _guarantee_call(rewriter.visit(expr)))
            found = True
            break
    if not found:
        raise ApprovalPolicyError(f"target {target!r} not found in program")

    statements: list[ast.stmt] = [ast.Import(names=[ast.alias(name="clauz3")])]
    statements += [
        ast.Import(names=[ast.alias(name=module, asname=alias_map[alias])])
        for alias, module in imports
    ]
    tree.body[0:0] = statements
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


class _AliasRewriter(ast.NodeTransformer):
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def visit_Name(self, node: ast.Name) -> ast.expr:
        renamed = self._mapping.get(node.id)
        if renamed is None:
            return node
        return ast.copy_location(ast.Name(id=renamed, ctx=node.ctx), node)


def _guarantee_call(expr: ast.expr) -> ast.expr:
    return ast.Call(
        func=ast.Attribute(
            value=ast.Name(id="clauz3", ctx=ast.Load()),
            attr="guarantee",
            ctx=ast.Load(),
        ),
        args=[expr],
        keywords=[],
    )


def _path_list(value: object) -> list[Path]:
    if not isinstance(value, list):
        return []
    return [Path(item) for item in value if isinstance(item, str)]


def _imports_from_dict(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        raise ApprovalPolicyError("approval policy 'imports' must be an object")
    imports: list[tuple[str, str]] = []
    for alias, module in value.items():
        if not isinstance(alias, str) or not alias.isidentifier():
            raise ApprovalPolicyError(f"import alias {alias!r} must be an identifier")
        if not isinstance(module, str) or not module:
            raise ApprovalPolicyError(f"import {alias!r} must map to a module string")
        imports.append((alias, module))
    return tuple(imports)


def _rule_from_dict(item: object) -> Rule:
    if not isinstance(item, dict):
        raise ApprovalPolicyError("each rule must be a JSON object")
    name = item.get("name")
    if not isinstance(name, str) or not name:
        raise ApprovalPolicyError("each rule must have a non-empty 'name'")
    decision = item.get("decision")
    if decision not in AUTO_DECISIONS:
        raise ApprovalPolicyError(
            f"rule {name!r} 'decision' must be one of {sorted(AUTO_DECISIONS)}",
        )
    key = _CLAUSE_KEY[decision]
    raw_clauses = item.get(key)
    if not isinstance(raw_clauses, list) or not raw_clauses:
        raise ApprovalPolicyError(
            f"rule {name!r} ({decision}) must have a non-empty {key!r} list",
        )
    reason = item.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ApprovalPolicyError(f"rule {name!r} 'reason' must be a string")
    return Rule(
        name=name,
        decision=decision,
        clauses=tuple(_clause(name, raw) for raw in raw_clauses),
        reason=reason,
    )


def _clause(rule_name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApprovalPolicyError(f"rule {rule_name!r} clauses must be expressions")
    try:
        ast.parse(value, mode="eval")
    except SyntaxError as exc:
        raise ApprovalPolicyError(
            f"rule {rule_name!r} clause {value!r} is not a valid expression: {exc}",
        ) from exc
    return value


def auto_approval_response(
    *,
    request_id: str,
    decision: PolicyDecision,
) -> dict[str, object]:
    """Build the approval response the service records for an auto-decision."""

    response: dict[str, object] = {
        "decision": decision.decision,
        "request_id": request_id,
    }
    if decision.reason:
        response["feedback"] = decision.reason
    if decision.decision in APPROVED_DECISIONS:
        response["receipt"] = f"auto-{request_id}"
    return response

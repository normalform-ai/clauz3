"""Execute proved programs on generated inputs and check their concrete effects.

Run the repository corpus with ``python -m clauz3.differential --root .``.
Trusted calls are intercepted: no trusted effect body is executed. This is a
bounded differential test, not another proof or a replacement runtime.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import math
import sys
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clauz3 import decorators
from clauz3.differential_boundary import BoundaryAssumptionError, validate_boundary
from clauz3.differential_contracts import (
    ContractEvaluationError,
    EffectCall,
    check_contract,
)
from clauz3.differential_inputs import ModelCase, ModelEffect, model_cases
from clauz3.imports_policy import ImportPolicy, build_policy
from clauz3.interp import DEFAULT_STEP_BUDGET, execute
from clauz3.layer_state import selected_layer
from clauz3.policy import load_trusted_effects
from clauz3.prover import ProofObservation, prove_text
from clauz3.row import Row


@dataclass(frozen=True)
class Finding:
    kind: str
    message: str


@dataclass(frozen=True)
class RunCheck:
    index: int
    inputs: object
    effects: object
    findings: tuple[Finding, ...]
    nonempty_queries: int = 0
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseReport:
    source: str
    status: str
    runs: tuple[RunCheck, ...] = ()
    reason: str = ""
    guarantees: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "checked" and not any(run.findings for run in self.runs)

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


class ReplayError(Exception):
    """An execution requested a trusted input absent from the sampled proof."""


class UnsupportedReplay(Exception):
    """A proved call cannot be replayed without executing a trusted body."""


def _is_effect_definition(func: Callable[..., Any]) -> bool:
    """Read the exact callable's declaration, using the prover's boundary rule.

    A function merely living in a trusted module is not necessarily a boundary:
    the prover inlines ordinary helpers, including helpers that invoke effects.
    Executing those helpers here would bypass interception of their inner calls.
    Definition location matters: unrelated helpers can share an effect's name,
    and a facade can re-export an effect under an entirely different name.
    """
    definition = inspect.unwrap(func)
    canonical = f"{definition.__module__}.{definition.__qualname__}"
    if not inspect.isfunction(definition):
        raise UnsupportedReplay(f"cannot inspect trusted definition: {canonical}")
    path = inspect.getsourcefile(definition)
    if path is None:
        raise UnsupportedReplay(f"cannot locate trusted definition: {canonical}")
    try:
        module = ast.parse(Path(path).read_text(), filename=path)
    except (OSError, SyntaxError) as exc:
        raise UnsupportedReplay(f"cannot read trusted definition: {canonical}") from exc
    for node in ast.walk(module):
        if not isinstance(node, ast.FunctionDef) or node.name != definition.__name__:
            continue
        first_line = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        if first_line == definition.__code__.co_firstlineno:
            return decorators.is_trusted_effect(node, decorators.import_aliases(module))
    raise UnsupportedReplay(f"cannot identify trusted definition: {canonical}")


def _data(value: Any) -> Any:
    """JSON display data with explicit tags for non-JSON value types."""
    if isinstance(value, type) and issubclass(value, Row):
        return {
            "schema": value.__qualname__,
            "module": "<entry>"
            if value.__module__ in {"__clauz3_synthetic__", "clauz3.row"}
            else value.__module__,
            "fields": {key: typ.__name__ for key, typ in value.__annotations__.items()},
        }
    if isinstance(value, Row):
        return {
            "row": _data(type(value)),
            "values": {
                key: _data(getattr(value, key)) for key in type(value).__annotations__
            },
        }
    if isinstance(value, dict):
        return {"dict": [[_data(key), _data(item)] for key, item in value.items()]}
    if isinstance(value, list):
        return [_data(item) for item in value]
    if isinstance(value, tuple):
        return {"tuple": [_data(item) for item in value]}
    if isinstance(value, (set, frozenset)):
        return {type(value).__name__: sorted((_data(item) for item in value), key=repr)}
    if isinstance(value, float) and not math.isfinite(value):
        return {"float": repr(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ReplayError(f"cannot serialize input of type {type(value).__name__}")


def _same_schema(left: type[Row], right: type[Row]) -> bool:
    def module(schema: type[Row]) -> str:
        # The prover and interpreter construct separate classes for an
        # entry-local declaration. Other trusted schema origins stay distinct.
        return (
            "<entry>"
            if schema.__module__ in {"__clauz3_synthetic__", "clauz3.row"}
            else schema.__module__
        )

    return (
        module(left) == module(right)
        and left.__qualname__ == right.__qualname__
        and left.__annotations__ == right.__annotations__
    )


def _same_value(left: Any, right: Any) -> bool:
    """Compare concrete effect values without JSON or Python type coercion.

    In particular, ``True``, ``1``, and ``1.0`` are different boundary values,
    as are lists, tuples, sets and frozensets. Treat every NaN as the same
    sampled floating-point value, while retaining the sign of zero.
    """
    if isinstance(left, type) and issubclass(left, Row):
        return (
            isinstance(right, type)
            and issubclass(right, Row)
            and _same_schema(left, right)
        )
    if isinstance(left, Row):
        return (
            isinstance(right, Row)
            and _same_schema(type(left), type(right))
            and all(
                _same_value(getattr(left, key), getattr(right, key))
                for key in type(left).__annotations__
            )
        )
    if type(left) is not type(right):
        return False
    if type(left) is float:
        if math.isnan(left) or math.isnan(right):
            return math.isnan(left) and math.isnan(right)
        return left == right and math.copysign(1.0, left) == math.copysign(1.0, right)
    if left is None or type(left) in (str, int, bool):
        return bool(left == right)
    if type(left) in (list, tuple):
        return len(left) == len(right) and all(
            _same_value(a, b) for a, b in zip(left, right, strict=True)
        )
    if type(left) is dict:
        # A normal dictionary lookup would identify True with 1, and a JSON
        # object would identify integer 1 with string "1". Match typed pairs.
        return _same_unordered(list(left.items()), list(right.items()))
    if type(left) in (set, frozenset):
        return _same_unordered(list(left), list(right))
    raise ReplayError(f"cannot compare input of type {type(left).__name__}")


def _same_unordered(left: list[Any], right: list[Any]) -> bool:
    if len(left) != len(right):
        return False
    remaining = list(right)
    for item in left:
        for index, other in enumerate(remaining):
            if _same_value(item, other):
                remaining.pop(index)
                break
        else:
            return False
    return True


@dataclass
class _Replay:
    sample: ModelCase
    policy: ImportPolicy
    pending: dict[str, deque[ModelEffect]] = field(init=False)
    signatures: dict[str, inspect.Signature] = field(default_factory=dict)
    call_names: dict[str, str] = field(default_factory=dict)
    effects: list[EffectCall] = field(default_factory=list)
    inputs: list[dict[str, Any]] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    nonempty_queries: int = 0
    boundaries: dict[Callable[..., Any], bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.pending = defaultdict(deque)
        for effect in self.sample.effects:
            self.pending[effect.qualified_name or effect.name].append(effect)

    def canonical_effects(self, effects: Sequence[EffectCall]) -> list[EffectCall]:
        """Use definition identities for checks, retaining import paths for reports."""
        return [(self.call_names[name], args, kwargs) for name, args, kwargs in effects]

    def dispatch(
        self, qualname: str, func: Callable[..., Any], args: Any, kwargs: Any
    ) -> Any:
        if not any(
            qualname.startswith(prefix + ".") for prefix in self.policy.trusted_prefixes
        ):
            # These are the interpreter's fixed, modelled library providers.
            # They are not trusted effects and must retain concrete semantics.
            return func(*args, **kwargs)
        # The interpreter records the path used to import a callable. A trusted
        # facade may re-export it under any name; the proof and contract DSL
        # address the original definition. Do not merge different definitions
        # merely because their short names happen to agree.
        definition = inspect.unwrap(func)
        canonical = f"{definition.__module__}.{definition.__qualname__}"
        if definition not in self.boundaries:
            self.boundaries[definition] = _is_effect_definition(definition)
        if not self.boundaries[definition]:
            raise UnsupportedReplay(
                f"trusted-module helper {canonical} is not a declared effect; "
                "replaying helper bodies with intercepted inner calls is unsupported"
            )
        self.call_names[qualname] = canonical
        self.effects.append((qualname, tuple(args), dict(kwargs)))
        signature = inspect.signature(func)
        self.signatures[canonical] = signature
        queue = self.pending.get(canonical)
        if queue is None:
            # Legacy observations lacking a qualified definition are keyed
            # only by name. Qualified facts never take this fallback.
            queue = self.pending.get(definition.__name__)
        if not queue:
            annotation = signature.return_annotation
            if annotation not in (None, type(None), "None"):
                raise ReplayError(f"unmodelled trusted input: {qualname}")
            # A void effect has exactly one possible return even when the
            # prover missed the call. Continue to collect the violating trace.
            expected = None
            result = None
        else:
            expected = queue.popleft()
            result = expected.result
        # Generic query schemas defined in the entry have a runtime class
        # distinct from the prover's synthetic class. Construct runtime rows.
        bound = signature.bind(*args, **kwargs)
        schemas = [
            value
            for value in bound.arguments.values()
            if isinstance(value, type) and issubclass(value, Row)
        ]
        if isinstance(result, list) and schemas:
            for row in result:
                if not isinstance(row, Row):
                    continue
                if not _same_schema(type(row), schemas[0]):
                    raise ReplayError(
                        "runtime query schema differs from sampled schema"
                    )
                if type(row).__module__ != "__clauz3_synthetic__" and (
                    type(row).__module__,
                    type(row).__qualname__,
                ) != (schemas[0].__module__, schemas[0].__qualname__):
                    raise ReplayError("runtime query has a different schema origin")
            result = [
                schemas[0](
                    **{key: getattr(row, key) for key in type(row).__annotations__}
                )
                if isinstance(row, Row)
                else row
                for row in result
            ]
        self.inputs.append({"call": qualname, "return": _data(result)})
        validate_boundary(func, args, kwargs, result)
        if (
            isinstance(result, list)
            and result
            and all(isinstance(row, Row) for row in result)
        ):
            # Keep the original schema identity for the native contract's
            # ColumnRef, even when execute constructed an entry-local class.
            assert expected is not None
            original = expected.result
            assert isinstance(original, list)
            self.rows.extend(original)
            self.nonempty_queries += 1
        return result


def _check_effects(
    effects: Sequence[EffectCall],
    expected: Sequence[ModelEffect],
    signatures: dict[str, inspect.Signature],
) -> list[Finding]:
    # Facts are guarded and quantified, not a chronological trace. Match an
    # unordered multiset: every observed occurrence needs its own proof fact.
    remaining = list(expected)
    findings = []
    for qualname, args, kwargs in effects:
        bound = signatures[qualname].bind(*args, **kwargs)
        bound.apply_defaults()
        actual = bound.arguments
        name = qualname.rsplit(".", 1)[-1]
        for index, fact in enumerate(remaining):
            if fact.qualified_name and fact.qualified_name != qualname:
                continue
            if fact.name != name:
                continue
            if all(
                key in actual and _same_value(actual[key], value)
                for key, value in fact.arguments.items()
            ):
                remaining.pop(index)
                break
        else:
            findings.append(
                Finding(
                    "effect_mismatch",
                    f"effect absent from sampled proof: {qualname}{_data(actual)!r}",
                )
            )
    return findings


def _run_sample(
    source: str,
    *,
    source_name: str,
    target: str,
    imports: list[Path],
    policy: ImportPolicy,
    observation: ProofObservation,
    sample: ModelCase,
    index: int,
    step_budget: int,
    signatures: dict[str, inspect.Signature],
) -> RunCheck:
    replay = _Replay(sample, policy, signatures=dict(signatures))
    findings: list[Finding] = []
    valid_inputs = True
    effects: Sequence[EffectCall] = replay.effects
    try:
        outcome = execute(
            source,
            source_name=source_name,
            target=target,
            policy=policy,
            import_roots=imports,
            trusted_dispatch=replay.dispatch,
            step_budget=step_budget,
        )
        effects = outcome.effects
    except UnsupportedReplay:
        # The attempted execution is incomplete, not a contract counterexample.
        # In particular, do not turn an inlined helper into a spurious effect.
        raise
    except BoundaryAssumptionError as exc:
        valid_inputs = False
        findings.append(Finding("assumption_mismatch", str(exc)))
    except Exception as exc:
        valid_inputs = False
        findings.append(Finding("execution_error", f"{type(exc).__name__}: {exc}"))
    try:
        canonical_effects = replay.canonical_effects(effects)
        findings.extend(
            _check_effects(canonical_effects, sample.effects, replay.signatures)
        )
        for index_contract, contract in enumerate(observation.contracts):
            if not valid_inputs:
                break
            if not check_contract(
                contract,
                canonical_effects,
                replay.signatures,
                row_sources=replay.rows,
            ):
                findings.append(
                    Finding(
                        "contract_violation",
                        f"guarantee {index_contract + 1} is false "
                        "on the concrete effects",
                    )
                )
    except (ContractEvaluationError, ReplayError, TypeError, ValueError) as exc:
        findings.append(Finding("oracle_error", f"{type(exc).__name__}: {exc}"))
    return RunCheck(
        index=index,
        inputs=replay.inputs,
        effects=_data(effects),
        findings=tuple(findings),
        nonempty_queries=replay.nonempty_queries,
        limitations=sample.limitations,
    )


def check_source(
    source: str,
    *,
    source_name: str = "<string>",
    target: str = "main",
    import_roots: Iterable[Path] = (),
    trusted_roots: Iterable[Path] = (),
    runs: int = 8,
    max_rows: int = 3,
    seed: int = 0,
    timeout_ms: int = 2000,
    step_budget: int = DEFAULT_STEP_BUDGET,
) -> CaseReport:
    """Prove one entry, sample its assumptions, and check concrete executions.

    ``runs`` is a maximum of distinct input models, not a promised run count:
    deterministic programs and finite input domains can exhaust earlier.
    Errors, refusals and exclusions remain explicit in the returned report.
    """
    if runs < 1 or max_rows < 0 or timeout_ms < 1 or step_budget < 1:
        raise ValueError(
            "runs, timeout_ms and step_budget must be positive; max_rows >= 0"
        )
    imports, trusted = list(import_roots), list(trusted_roots)
    completed: list[RunCheck] = []
    try:
        with selected_layer(trusted_roots=trusted, import_paths=imports):
            proofs = prove_text(
                source,
                source_name=source_name,
                target=target,
                import_paths=imports,
                trusted_roots=trusted,
                capture_observations=True,
            )
            if len(proofs) != 1:
                return CaseReport(
                    source_name, "error", reason="expected exactly one target theorem"
                )
            proof = proofs[0]
            if not proof.ok:
                return CaseReport(source_name, "not_proved", reason=str(proof.proof))
            node = next(
                node
                for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == target
            )
            if node.args.args or node.args.posonlyargs or node.args.kwonlyargs:
                return CaseReport(
                    source_name,
                    "parameterized",
                    reason="interpreter entry is called without arguments",
                )
            observation = proof.observation
            if observation is None:
                return CaseReport(
                    source_name, "error", reason="OK proof lacks observations"
                )
            policy = build_policy(trusted_roots=trusted, import_roots=imports)
            signatures = {
                name: inspect.Signature(
                    [
                        inspect.Parameter(
                            parameter, inspect.Parameter.POSITIONAL_OR_KEYWORD
                        )
                        for parameter in parameters
                    ]
                )
                for name, parameters in load_trusted_effects(trusted).items()
            }
            for index, sample in enumerate(
                model_cases(
                    observation,
                    runs=runs,
                    max_rows=max_rows,
                    seed=seed,
                    timeout_ms=timeout_ms,
                )
            ):
                completed.append(
                    _run_sample(
                        source,
                        source_name=source_name,
                        target=target,
                        imports=imports,
                        policy=policy,
                        observation=observation,
                        sample=sample,
                        index=index,
                        step_budget=step_budget,
                        signatures=signatures,
                    )
                )
            if not completed:
                return CaseReport(
                    source_name,
                    "incomplete",
                    reason="no satisfying bounded input model",
                )
            return CaseReport(
                source_name,
                "checked",
                tuple(completed),
                guarantees=observation.guarantees,
            )
    except Exception as exc:
        return CaseReport(
            source_name,
            "incomplete",
            tuple(completed),
            reason=f"{type(exc).__name__}: {exc}",
        )


def corpus_cases(root: Path) -> list[Path]:
    return sorted(
        [
            *(root / "examples").rglob("cases/*.py"),
            *(root / "src/clauz3/stdlib").rglob("tests/cases/*.py"),
        ]
    )


def corpus_roots(case: Path) -> tuple[list[Path], list[Path]]:
    """Resolve the repository's example and stdlib layer conventions."""
    for parent in case.parents:
        tools = parent / "tools"
        if not tools.is_dir():
            continue
        siblings = (
            [
                path
                for path in sorted(parent.parent.iterdir())
                if (path / "tools").is_dir() and path != parent
            ]
            if "stdlib" in parent.parts
            else []
        )
        imports = [parent, *siblings]
        trusted = [
            path
            for root in imports
            for path in sorted((root / "tools").glob("*/trusted"))
        ]
        return imports, trusted
    raise ValueError(f"no tools root for {case}")


def check_path(path: Path, **options: Any) -> CaseReport:
    imports, trusted = corpus_roots(path)
    return check_source(
        path.read_text(),
        source_name=str(path),
        import_roots=imports,
        trusted_roots=trusted,
        **options,
    )


def summarize(reports: Sequence[CaseReport]) -> dict[str, Any]:
    statuses: dict[str, int] = defaultdict(int)
    findings: dict[str, int] = defaultdict(int)
    for report in reports:
        statuses[report.status] += 1
        for run in report.runs:
            for finding in run.findings:
                findings[finding.kind] += 1
    return {
        "programs": len(reports),
        "statuses": dict(statuses),
        "runs": sum(len(report.runs) for report in reports),
        "nonempty_queries": sum(
            run.nonempty_queries for report in reports for run in report.runs
        ),
        "findings": dict(findings),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--runs", type=int, default=8)
    parser.add_argument("--max-rows", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout-ms", type=int, default=2000)
    parser.add_argument("--json", type=Path, dest="json_path")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.max_rows < 0 or args.timeout_ms < 1:
        parser.error(
            "runs and timeout-ms must be positive; max-rows must be nonnegative"
        )
    cases = args.cases or corpus_cases(args.root)
    if not cases:
        parser.error("no corpus programs found")
    reports = []
    for index, path in enumerate(cases, 1):
        report = check_path(
            path,
            runs=args.runs,
            max_rows=args.max_rows,
            seed=args.seed,
            timeout_ms=args.timeout_ms,
        )
        reports.append(report)
        if args.verbose:
            print(
                f"{index}/{len(cases)} {path}: {report.status} "
                f"({len(report.runs)} runs)",
                file=sys.stderr,
                flush=True,
            )
    summary = summarize(reports)
    payload = {
        "configuration": {
            "runs": args.runs,
            "max_rows": args.max_rows,
            "seed": args.seed,
            "timeout_ms": args.timeout_ms,
        },
        "summary": summary,
        "cases": [report.as_dict() for report in reports],
    }
    if args.json_path:
        args.json_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n"
        )
    print(json.dumps(summary, indent=2))
    for report in reports:
        if report.status in {"error", "incomplete"}:
            print(f"{report.source}: {report.status}: {report.reason}")
        for run in report.runs:
            for finding in run.findings:
                print(
                    f"{report.source} run {run.index}: "
                    f"{finding.kind}: {finding.message}"
                )
    return int(
        any(
            report.status in {"error", "incomplete"}
            or any(run.findings for run in report.runs)
            for report in reports
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

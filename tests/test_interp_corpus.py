"""Run the whole example/stdlib case corpus through the interpreter.

Coverage evidence for the prototype: if the interpreter cannot run the programs
the project already ships, the subset is too small to be the runtime.

Trusted effects are stubbed via ``trusted_dispatch`` so the sweep performs no
real I/O -- which is itself a property of owning the interpreter: the hand-off
to CPython happens in exactly one place, so intercepting it is a parameter
rather than a monkeypatch.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import astroid
import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import UnsupportedNode, execute
from clauz3.prover import prove_path
from clauz3.runner import (
    RunConfigError,
    _preflight_nodes,
    _preflight_structure,
    discover_trusted_roots,
)

ROOT = Path(__file__).resolve().parents[1]
STDLIB = ROOT / "src/clauz3/stdlib"
EXAMPLES = ROOT / "examples"

#: Lower bound on cases the interpreter must run, so the sweep cannot quietly
#: degenerate into "everything was classified as a fixture". Deliberately not an
#: exact count: adding a case should not fail this test for a reason unrelated
#: to the interpreter.
MIN_INTERPRETED = 200


def _all_case_files() -> list[Path]:
    return sorted([*STDLIB.rglob("tests/cases/*.py"), *EXAMPLES.rglob("cases/*.py")])


def _cases() -> list[Path]:
    # Unsupported fixtures have separate prover/interpreter/preflight checks.
    return [case for case in _all_case_files() if not case.name.endswith("_skip.py")]


def _skip_cases() -> list[Path]:
    return [case for case in _all_case_files() if case.name.endswith("_skip.py")]


def _roots_for(case: Path) -> tuple[list[Path], list[Path]]:
    """Locate a case's import roots and trusted roots by directory convention."""
    for parent in case.parents:
        tools = parent / "tools"
        if not tools.is_dir():
            continue
        siblings: list[Path] = []
        if "stdlib" in parent.parts:
            # stdlib packages cross-import (find -> filesystem), so every
            # sibling package is an import root too.
            siblings = [
                path
                for path in sorted(parent.parent.iterdir())
                if (path / "tools").is_dir() and path != parent
            ]
        roots = [parent, *siblings]
        # Nested domains (tools/google_workspace/gmail/trusted) follow the
        # same discovery rule as the runner and `clauz3 install`.
        return roots, discover_trusted_roots(import_roots=roots)
    return [], []


def _has_parameters(case: Path) -> bool:
    """True when the case's ``main`` takes arguments.

    Classified from the source rather than by substring-matching the error
    message: matching on ``"missing argument"`` would silently reclassify a
    genuine arity bug in the interpreter as a prove-only fixture.
    """
    module = astroid.parse(case.read_text(), path=str(case))
    for stmt in module.body:
        if isinstance(stmt, astroid.FunctionDef) and stmt.name == "main":
            arguments = stmt.args
            if arguments.args or arguments.posonlyargs or arguments.kwonlyargs:
                return True
    return False


def _stub(qualname: str, func: Any, args: Any, kwargs: Any) -> Any:
    """Synthesize a subset return value from the trusted function's annotation."""
    try:
        annotation = inspect.signature(func).return_annotation
    except (ValueError, TypeError):  # pragma: no cover - defensive
        return None
    name = getattr(annotation, "__name__", str(annotation))
    if "list" in str(annotation):
        return []
    return {"str": "", "int": 0, "bool": False, "float": 0.0}.get(name)


def _runnable_cases() -> list[Path]:
    # Cases whose ``main`` takes arguments are prove-only fixtures: ``clauz3
    # run`` calls ``main()`` with no arguments and cannot run them either.
    return [case for case in _cases() if not _has_parameters(case)]


def _case_id(case: Path) -> str:
    return case.relative_to(ROOT).as_posix()


def test_corpus_is_mostly_runnable() -> None:
    # Every case is accounted for, and the runnable majority is large enough
    # that the per-case sweep below cannot quietly shrink to nothing.
    runnable = _runnable_cases()
    parameterised = [case for case in _cases() if _has_parameters(case)]
    assert len(runnable) + len(parameterised) == len(_cases())
    assert len(runnable) >= MIN_INTERPRETED


# One test per case, so the sweep spreads across pytest-xdist workers instead
# of running as a single ~8 minute test, and a failure names its case.
#
# Isolation comes from conftest's autouse layer eviction, which drops each
# case's ``tools.*`` modules *and* their ``FUNCTIONS``/``REGISTERED_CONTRACTS``
# entries. Removing the modules first (as a local fixture used to) hides them
# from that eviction and leaks the registrations to later tests on the worker.
@pytest.mark.parametrize("case", _runnable_cases(), ids=_case_id)
def test_interpreter_runs_corpus_case(case: Path) -> None:
    import_roots, trusted_roots = _roots_for(case)
    assert import_roots, f"{case}: no tools root"
    execute(
        case.read_text(),
        source_name=str(case),
        policy=build_policy(
            trusted_roots=trusted_roots,
            import_roots=import_roots,
        ),
        import_roots=import_roots,
        trusted_dispatch=_stub,
    )


def test_preflight_admits_every_case_the_interpreter_runs() -> None:
    """``runner._preflight_structure`` must not be narrower than ``execute``.

    The pre-flight hoists the subset-vocabulary check ahead of the approval
    round trip, which makes it a *second* statement of the subset -- and a
    second statement can disagree. It did: ``astroid.Starred`` was in neither
    registry nor the scaffolding tuple, so ``A, *B = (1, 2, 3)`` ran under
    ``execute`` and was refused by ``clauz3 run``.

    Being straight about what this test is worth: the corpus contains no
    ``Starred``, so it would **not** have caught that regression. What catches
    it is ``test_runtime_module_level._validate`` calling the shipped
    pre-flight instead of approximating it. This test covers the other
    direction -- it pins the scaffolding tuple against every construct the
    shipped corpus actually uses, so dropping an entry that real programs
    depend on fails here rather than in the field.

    One-directional on purpose: refusing here what ``execute`` would refuse
    later is the whole point, so this asserts only that the pre-flight admits
    everything the interpreter accepts.
    """
    refused: list[str] = []
    for case in _cases():
        if _has_parameters(case):
            continue
        import_roots, trusted_roots = _roots_for(case)
        if not import_roots:  # pragma: no cover
            continue
        # The shipped node gate, without ``target_node``: that half executes
        # module level for real, and ``test_interpreter_runs_corpus_case``
        # already covers it. An earlier version carried its own copy of the
        # walk -- a third statement of the subset that could drift.
        module = astroid.parse(case.read_text(), path=str(case))
        try:
            _preflight_nodes(module, source_name=str(case))
        except RunConfigError as exc:
            refused.append(f"{case}: {exc}")
    assert not refused, (
        "the pre-flight node gate refuses cases the interpreter runs:\n"
        + "\n".join(refused)
    )


# Interpreter, prover, and shipped preflight refusal reasons. Set equality
# below prevents adding an untested skip case or retaining a stale exemption.
_SKIP_REFUSALS = {
    "examples/email/cases/while_loop_skip.py": (
        r"While is outside the proved subset",
        r"^unsupported ast node While at line 15: while n < 2",
        r"While is not part of the proved subset",
    ),
}


def test_interpreter_refuses_skip_cases(
    trusted_layer_cache_scope: Callable[[], AbstractContextManager[None]],
) -> None:
    """Every excluded skip fixture fails at its documented construct in all gates."""
    skips = _skip_cases()
    keys = {case.relative_to(ROOT).as_posix() for case in skips}
    assert keys == set(_SKIP_REFUSALS), (
        "Register each skip case's refusal reasons and prune removed cases: "
        f"{sorted(keys ^ set(_SKIP_REFUSALS))}"
    )
    for case in skips:
        # Scope each case's layer imports and contract registrations to it.
        with trusted_layer_cache_scope():
            _assert_skip_case_refused(case)


def _assert_skip_case_refused(case: Path) -> None:
    interpreter_match, prover_match, preflight_match = _SKIP_REFUSALS[
        case.relative_to(ROOT).as_posix()
    ]
    # Missing arguments would prevent the body reaching the pinned refusal.
    assert not _has_parameters(case), case
    import_roots, trusted_roots = _roots_for(case)
    policy = build_policy(trusted_roots=trusted_roots, import_roots=import_roots)
    with pytest.raises(UnsupportedNode, match=interpreter_match):
        execute(
            case.read_text(),
            source_name=str(case),
            policy=policy,
            import_roots=import_roots,
            trusted_dispatch=_stub,
        )
    with pytest.raises(RunConfigError, match=preflight_match):
        _preflight_structure(
            source=case.read_text(),
            source_name=str(case),
            target="main",
            policy=policy,
            import_roots=import_roots,
        )
    results = prove_path(
        case,
        trusted_roots=trusted_roots,
        import_roots=import_roots,
    )
    assert len(results) == 1
    assert results[0].proof.conclusion is Conclusion.SKIP
    assert re.search(prover_match, str(results[0].proof.error)), (
        f"{case}: expected prover refusal matching {prover_match!r}, "
        f"got {results[0].proof.error!r}"
    )

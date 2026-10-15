"""Python start offsets determine which string-search branches emit effects."""

import importlib
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.imports_policy import build_policy
from clauz3.interp import execute
from clauz3.prover import prove_path
from clauz3.runner import RunProofError, run_source


def _program(
    tmp_path: Path, *, needle: str, start: int, expected: int, minimum: int
) -> tuple[Path, Path]:
    package = tmp_path / f"string_find_{uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "effects.py").write_text(
        "import clauz3\n"
        "EVENTS: list[str] = []\n"
        "@clauz3.trusted\n"
        "def record(value: str) -> None:\n"
        "    EVENTS.append(value)\n"
    )
    (package / "contracts.py").write_text(
        "from clauz3.spec import ContractSpec, contract, effect\n"
        "@contract\n"
        "def enough_records() -> ContractSpec:\n"
        f"    return effect('record').count() >= {minimum}\n"
    )
    entry = tmp_path / "plan.py"
    entry.write_text(
        "import clauz3\n"
        f"from {package.name}.effects import record\n"
        f"from {package.name}.contracts import enough_records\n"
        "@clauz3.guarantee(enough_records())\n"
        "def main() -> None:\n"
        "    record('first')\n"
        f"    if 'abc'.find({needle!r}, {start}) == {expected}:\n"
        "        record('second')\n"
    )
    return entry, package


@pytest.mark.parametrize(
    ("needle", "start", "expected", "minimum", "conclusion", "events"),
    [
        ("b", -2, -1, 2, Conclusion.FAIL, ["first"]),
        ("b", -2, 1, 2, Conclusion.OK, ["first", "second"]),
        ("b", -2, 1, 3, Conclusion.FAIL, ["first", "second"]),
        ("b", 1, 1, 2, Conclusion.OK, ["first", "second"]),
        ("missing", -2, -1, 2, Conclusion.OK, ["first", "second"]),
    ],
    ids=[
        "negative-start-cannot-invent-second-record",
        "correct-negative-start",
        "stronger-guarantee",
        "normalized-positive-start",
        "legitimate-not-found",
    ],
)
def test_find_start_controls_the_guaranteed_effect_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    needle: str,
    start: int,
    expected: int,
    minimum: int,
    conclusion: Conclusion,
    events: list[str],
) -> None:
    entry, package = _program(
        tmp_path, needle=needle, start=start, expected=expected, minimum=minimum
    )
    source = entry.read_text()

    proofs = prove_path(entry, trusted_roots=[package], import_roots=[tmp_path])

    assert [proof.proof.conclusion for proof in proofs] == [conclusion]
    assert "guarantee" in str(proofs[0].proof)
    effects = importlib.import_module(f"{package.name}.effects")
    assert effects.__file__ is not None
    assert Path(effects.__file__).resolve() == (package / "effects.py").resolve()
    assert effects.EVENTS == []

    if conclusion is Conclusion.FAIL:
        approve = Mock(side_effect=AssertionError("approval must not be reached"))
        execute_source = Mock(
            side_effect=AssertionError("execution must not be reached")
        )
        monkeypatch.setattr("clauz3.runner.submit_approval_request", approve)
        monkeypatch.setattr("clauz3.runner._execute_source", execute_source)
        with pytest.raises(RunProofError) as exc_info:
            run_source(
                source,
                source_name=str(entry),
                trusted_roots=[package],
                import_roots=[tmp_path],
                approval_service_url="http://approval.invalid",
            )
        assert [proof.proof.conclusion for proof in exc_info.value.proofs] == [
            Conclusion.FAIL
        ]
        approve.assert_not_called()
        execute_source.assert_not_called()
        assert effects.EVENTS == []

    # The interpreter runs CPython's search. Its actual effects must agree
    # with the branch oracle, including a genuine not-found result of -1.
    oracle_events = ["first"]
    if "abc".find(needle, start) == expected:
        oracle_events.append("second")
    assert oracle_events == events
    outcome = execute(
        source,
        source_name=str(entry),
        policy=build_policy(trusted_roots=[package], import_roots=[tmp_path]),
        import_roots=[tmp_path],
    )
    assert events == effects.EVENTS
    assert outcome.effects == tuple(
        (f"{package.name}.effects.record", (value,), {}) for value in events
    )

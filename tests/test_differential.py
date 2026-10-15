"""End-to-end soundness checks, including deliberate semantic mutations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from clauz3.differential import check_source
from clauz3.interp import interpreter


@pytest.fixture
def layer(tmp_path: Path) -> tuple[Path, Path]:
    trusted = tmp_path / "difftools"
    trusted.mkdir()
    (trusted / "__init__.py").write_text("")
    (trusted / "effects.py").write_text("""
import clauz3

class Item(clauz3.Row):
    value: int
    enabled: bool
    label: str

@clauz3.trusted
@clauz3.ensures(lambda result: len(result) <= 2)
def read_items() -> list[Item]:
    raise AssertionError("the harness must never execute a trusted body")

@clauz3.trusted
def read_text() -> str:
    raise AssertionError("the harness must never execute a trusted body")

@clauz3.trusted
def emit_text(value: str) -> None:
    raise AssertionError("the harness must never execute a trusted body")

@clauz3.trusted
def emit(value: int) -> None:
    raise AssertionError("the harness must never execute a trusted body")

@clauz3.trusted
@clauz3.ensures(lambda value, result: result == value)
def echo(value: int) -> int:
    raise AssertionError("the harness must never execute a trusted body")
""")
    (trusted / "contracts.py").write_text("""
from clauz3.spec import contract, effect
from difftools.effects import emit, emit_text

@contract
def text_output():
    return effect(emit_text).all(lambda e: len(e.value) >= 0)

@contract
def bounded(limit):
    return effect(emit).all(lambda e: e.value <= limit)

@contract
def none():
    return effect(emit).empty()
""")
    return tmp_path, trusted


def _check(layer: tuple[Path, Path], body: str, **kwargs: Any) -> Any:
    imports, trusted = layer
    source = (
        """
import clauz3
from difftools.effects import echo, emit, read_items
from difftools.contracts import bounded
@clauz3.guarantee(bounded(1))
def main() -> None:
"""
        + body
    )
    return check_source(
        source, import_roots=[imports], trusted_roots=[trusted], **kwargs
    )


def test_deterministic_program_exhausts_one_model_without_effect_io(
    layer: tuple[Path, Path],
) -> None:
    report = _check(layer, "    emit(1)\n", runs=8)
    assert report.ok, report
    assert len(report.runs) == 1
    assert report.runs[0].effects
    assert report.runs[0].inputs == [{"call": "difftools.effects.emit", "return": None}]


def test_generated_rows_exercise_query_loop(layer: tuple[Path, Path]) -> None:
    report = _check(
        layer,
        """    for row in read_items():
        if row.enabled:
            emit(1)
""",
        runs=8,
    )
    assert report.ok, report
    assert len(report.runs) > 1
    assert sum(run.nonempty_queries for run in report.runs) > 0
    assert any("emit" in str(run.effects) for run in report.runs)
    assert len({str(run.inputs) for run in report.runs}) == len(report.runs)


@pytest.mark.parametrize(
    "body",
    [
        "    emit(1 + 0)\n",
        "    x = 1 + 0\n    emit(x)\n",
        "    def helper():\n        return 1 + 0\n    emit(helper())\n",
        "    if 1 + 0 == 2:\n        emit(2)\n",
    ],
)
def test_checker_detects_semantic_drift_in_multiple_positions(
    layer: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    # Mutation test: the prover still adds correctly, while the interpreter
    # returns an incorrect concrete value. The independent oracle must catch it.
    monkeypatch.setitem(interpreter._BINOPS, "+", lambda a, b: a + b + 1)
    report = _check(layer, body)
    assert report.status == "checked", report
    assert not report.ok
    kinds = {finding.kind for run in report.runs for finding in run.findings}
    assert "contract_violation" in kinds
    assert "effect_mismatch" in kinds


def test_failed_proof_is_accounted_for_without_running(
    layer: tuple[Path, Path],
) -> None:
    report = _check(layer, "    emit(2)\n")
    assert report.status == "not_proved"
    assert not report.runs


def test_execution_refusal_is_not_a_successful_run(layer: tuple[Path, Path]) -> None:
    report = _check(layer, "    emit(1)\n", step_budget=1)
    assert not report.ok
    assert any(f.kind == "execution_error" for run in report.runs for f in run.findings)


def test_invalid_environment_is_not_reported_as_contract_counterexample(
    layer: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(interpreter._BINOPS, "+", lambda a, b: a + b + 1)
    report = _check(layer, "    emit(echo(1 + 0))\n")
    assert report.status == "checked", report
    kinds = {finding.kind for run in report.runs for finding in run.findings}
    assert "assumption_mismatch" in kinds
    assert "effect_mismatch" in kinds
    assert "contract_violation" not in kinds


def test_multiple_returning_sites_in_loop_replay_in_execution_order(
    layer: tuple[Path, Path],
) -> None:
    report = _check(
        layer,
        """    for row in read_items():
        emit(echo(0))
        emit(echo(1))
""",
        runs=4,
        max_rows=2,
    )
    assert report.ok, report
    assert any(run.nonempty_queries for run in report.runs)
    assert all(not run.limitations for run in report.runs)


_NONE_RETURN_COMPARISON = """    value = emit(0)
    if value != False:
        emit(2)
"""


def test_none_return_comparison_refuses_false_guarantee(
    layer: tuple[Path, Path],
) -> None:
    # Avoid a bare return, which is refused independently of void comparisons.
    # The old False surrogate hid emit(2), yielding an unsound OK proof.
    report = _check(layer, _NONE_RETURN_COMPARISON)
    assert report.status == "not_proved", report
    assert not report.runs


@pytest.mark.parametrize(
    "body",
    [
        "    emit_text(read_text())\n",
        "    for row in read_items():\n        emit_text(row.label)\n",
    ],
)
def test_full_unicode_trusted_values_replay_through_interpreter(
    layer: tuple[Path, Path], body: str
) -> None:
    imports, trusted = layer
    source = (
        """
import clauz3
from difftools.effects import emit_text, read_text, read_items
from difftools.contracts import text_output
@clauz3.guarantee(text_output())
def main() -> None:
"""
        + body
    )
    report = check_source(
        source, import_roots=[imports], trusted_roots=[trusted], runs=8, max_rows=2
    )
    assert report.ok, report
    emitted = set()
    for run in report.runs:
        assert isinstance(run.effects, dict)
        for effect in run.effects["tuple"]:
            name, args, _ = effect["tuple"]
            if name.endswith("emit_text"):
                emitted.add(args["tuple"][0])
    assert emitted >= {"\U00030000", "\U0010ffff", "\ud800"}

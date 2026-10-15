"""Effect agreement preserves Python value types independently of contracts."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from clauz3.differential import _check_effects, _data, check_source
from clauz3.differential_inputs import ModelEffect
from clauz3.interp import interpreter
from clauz3.row import Row


def emit(value: object) -> None:
    raise AssertionError("trace comparison must never call effects")


def mismatches(actual: object, expected: object) -> bool:
    findings = _check_effects(
        [("trusted.emit", (actual,), {})],
        [ModelEffect(0, "emit", "trusted.emit", {"value": expected}, None)],
        {"trusted.emit": inspect.signature(emit)},
    )
    assert all(finding.kind == "effect_mismatch" for finding in findings)
    return bool(findings)


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ((1,), [1]),
        ({1}, [1]),
        ({1}, frozenset({1})),
        ({1: "a"}, {"1": "a"}),
        ({True: "a"}, {1: "a"}),
        (True, 1),
        (1, 1.0),
        (None, False),
        ([{"nested": (1,)}], [{"nested": [1]}]),
        ({(True,): {1}}, {(1,): {1}}),
        ({"values": {True}}, {"values": {1}}),
        (0.0, -0.0),
        (float("inf"), float("-inf")),
        (float("nan"), 0.0),
    ],
)
def test_type_and_nested_value_drift_is_an_effect_mismatch(
    actual: object, expected: object
) -> None:
    assert mismatches(actual, expected)
    assert mismatches(expected, actual)


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ([1, "a", True], [1, "a", True]),
        ((1, "a", True), (1, "a", True)),
        ({1, "a", (2,)}, {(2,), "a", 1}),
        (frozenset({1, "a"}), frozenset({"a", 1})),
        ({1: ["a"], "1": {2, 3}}, {"1": {3, 2}, 1: ["a"]}),
        (float("nan"), float("nan")),
        (float("inf"), float("inf")),
        (-0.0, -0.0),
        (None, None),
    ],
)
def test_equal_values_match_including_unordered_containers(
    actual: object, expected: object
) -> None:
    assert not mismatches(actual, expected)


def _schema(module: str, name: str = "Item") -> type[Row]:
    return type(
        name,
        (Row,),
        {"__module__": module, "__annotations__": {"value": int}},
    )


def test_schema_matching_preserves_origins_and_normalizes_entry_classes() -> None:
    original = _schema("trusted.rows")
    reloaded = _schema("trusted.rows")
    alien = _schema("another.rows")
    synthetic = _schema("__clauz3_synthetic__")
    runtime = _schema("clauz3.row")
    assert not mismatches(original, reloaded)
    assert not mismatches(original(value=1), reloaded(value=1))
    assert not mismatches(synthetic, runtime)
    assert not mismatches(synthetic(value=1), runtime(value=1))
    assert mismatches(original, alien)
    assert mismatches(original(value=1), alien(value=1))
    assert mismatches(original(value=True), original(value=1))
    assert mismatches(original(value=1), original(value=2))
    assert mismatches(synthetic, _schema("clauz3.row", name="OtherItem"))


def test_json_diagnostics_retain_container_and_dictionary_key_types() -> None:
    value = {1: (True,), "1": [1], (2,): {3}, 4: frozenset({5})}
    encoded = json.loads(json.dumps(_data(value), allow_nan=False))
    assert encoded == {
        "dict": [
            [1, {"tuple": [True]}],
            ["1", [1]],
            [{"tuple": [2]}, {"set": [3]}],
            [4, {"frozenset": [5]}],
        ]
    }
    assert _data(float("nan")) == {"float": "nan"}
    assert _data(float("inf")) == {"float": "inf"}


@pytest.mark.parametrize(
    ("expression", "mutation"),
    [
        ("[1] + []", (1,)),
        ("{1 + 0: 'a'}", "1"),
        ("1 + 0", True),
    ],
)
def test_no_guarantees_still_detects_type_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expression: str,
    mutation: Any,
) -> None:
    trusted = tmp_path / "trace_tools"
    trusted.mkdir()
    (trusted / "__init__.py").write_text("")
    (trusted / "effects.py").write_text("""
import clauz3

@clauz3.trusted
def emit(value) -> None:
    raise AssertionError("trusted effect body must never run")
""")
    (trusted / "contracts.py").write_text("""
from clauz3.spec import contract, no_guarantees

@contract
def unrestricted():
    return no_guarantees()
""")
    monkeypatch.setitem(interpreter._BINOPS, "+", lambda a, b: mutation)
    source = f"""
import clauz3
from trace_tools.effects import emit
from trace_tools.contracts import unrestricted

@clauz3.guarantee(unrestricted())
def main() -> None:
    emit({expression})
"""
    report = check_source(
        source,
        import_roots=[tmp_path],
        trusted_roots=[trusted],
        runs=1,
    )
    assert report.status == "checked", report
    assert not report.ok
    kinds = {finding.kind for run in report.runs for finding in run.findings}
    assert kinds == {"effect_mismatch"}, report

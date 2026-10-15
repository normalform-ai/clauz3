"""Re-exported trusted effects retain their defining callable identity."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import z3

from clauz3.differential import ReplayError, _check_effects, _Replay, check_source
from clauz3.differential_inputs import ModelCase, ModelEffect
from clauz3.imports_policy import ImportPolicy


@pytest.fixture
def layer(tmp_path: Path) -> tuple[Path, Path]:
    trusted = tmp_path / "reexporttools"
    trusted.mkdir()
    (trusted / "__init__.py").write_text("")
    (trusted / "effects.py").write_text("""
import clauz3

@clauz3.trusted
def emit(value: int) -> None:
    raise AssertionError("the harness must never execute a trusted body")

@clauz3.trusted
@clauz3.ensures(lambda value, result: result == value)
def echo(value: int) -> int:
    raise AssertionError("the harness must never execute a trusted body")
""")
    (trusted / "facade.py").write_text("""
from reexporttools.effects import emit, emit as send, echo as fetch
""")
    (trusted / "contracts.py").write_text("""
from clauz3.spec import contract, effect
from reexporttools.effects import emit

@contract
def at_least(n):
    return effect(emit).count() >= n

@contract
def bounded(limit):
    return effect(emit).all(lambda call: call.value <= limit)
""")
    return tmp_path, trusted


@pytest.mark.parametrize("name", ["emit", "send"])
def test_reexported_void_effect_matches_proof_and_native_contract(
    layer: tuple[Path, Path], name: str
) -> None:
    imports, trusted = layer
    report = check_source(
        f"""
import clauz3
from reexporttools.facade import {name}
from reexporttools.contracts import at_least, bounded
@clauz3.guarantee(at_least(1))
@clauz3.guarantee(bounded(1))
def main() -> None:
    {name}(1)
""",
        import_roots=[imports],
        trusted_roots=[trusted],
    )
    assert report.ok, report
    assert len(report.runs) == 1
    assert f"reexporttools.facade.{name}" in str(report.runs[0].effects)


def test_direct_and_renamed_returning_reexports_share_ordered_inputs(
    layer: tuple[Path, Path],
) -> None:
    imports, trusted = layer
    report = check_source(
        """
import clauz3
from reexporttools.effects import echo, emit
from reexporttools.facade import fetch, send
from reexporttools.contracts import at_least, bounded
@clauz3.guarantee(at_least(2))
@clauz3.guarantee(bounded(2))
def main() -> None:
    emit(echo(1))
    send(fetch(2))
""",
        import_roots=[imports],
        trusted_roots=[trusted],
    )
    assert report.ok, report
    assert len(report.runs) == 1
    assert report.runs[0].inputs == [
        {"call": "reexporttools.effects.echo", "return": 1},
        {"call": "reexporttools.effects.emit", "return": None},
        {"call": "reexporttools.facade.fetch", "return": 2},
        {"call": "reexporttools.facade.send", "return": None},
    ]
    assert "reexporttools.facade.send" in str(report.runs[0].effects)


def test_different_definitions_with_same_short_name_do_not_share_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted = tmp_path / "identitytools"
    trusted.mkdir()
    (trusted / "__init__.py").write_text("")
    for module in ["left", "right"]:
        (trusted / f"{module}.py").write_text("""
import clauz3

@clauz3.trusted
def read() -> int:
    raise AssertionError("the harness must never execute a trusted body")
""")
    monkeypatch.syspath_prepend(str(tmp_path))
    left = importlib.import_module("identitytools.left").read
    right = importlib.import_module("identitytools.right").read
    sample = ModelCase(
        z3.Model(),
        {},
        (ModelEffect(0, "read", "identitytools.left.read", {}, 1),),
    )
    replay = _Replay(
        sample,
        ImportPolicy(
            allowed_roots=frozenset(),
            allowed_prefixes=("identitytools",),
            trusted_prefixes=("identitytools",),
        ),
    )
    with pytest.raises(ReplayError, match="unmodelled trusted input"):
        replay.dispatch("identitytools.facade.right", right, (), {})
    assert replay.dispatch("identitytools.facade.left", left, (), {}) == 1
    canonical = replay.canonical_effects(replay.effects)
    findings = _check_effects(canonical, sample.effects, replay.signatures)
    assert len(findings) == 1
    assert "identitytools.right.read" in findings[0].message

"""Trusted-module helpers are explicit replay gaps, not effect counterexamples."""

from __future__ import annotations

from pathlib import Path

import pytest

from clauz3.differential import check_source


@pytest.fixture
def layer(tmp_path: Path) -> tuple[Path, Path, Path]:
    trusted = tmp_path / "helpertools"
    trusted.mkdir()
    (trusted / "__init__.py").write_text("")
    sentinel = tmp_path / "effect-body-executed"
    (trusted / "effects.py").write_text(f"""
from pathlib import Path
import clauz3 as c
import deal as d

@c.trusted
def emit(value: int) -> None:
    Path({str(sentinel)!r}).write_text("executed")
    raise AssertionError("the harness must never execute a trusted body")

@d.has("io")
def legacy(value: int) -> None:
    Path({str(sentinel)!r}).write_text("executed")
    raise AssertionError("the harness must never execute a trusted body")

@d.chain(d.has("io"), d.pre(lambda value: value >= 0))
def chained(value: int) -> None:
    Path({str(sentinel)!r}).write_text("executed")
    raise AssertionError("the harness must never execute a trusted body")
""")
    (trusted / "helpers.py").write_text("""
from helpertools.effects import emit as effect
import deal as d

def pure(value: int) -> int:
    return value + 1

def effectful(value: int) -> int:
    effect(value)
    return value

def effectful_void(value: int) -> None:
    effect(value)

def emit(value: int) -> int:
    return value

@d.has()
def unmarked(value: int) -> int:
    return value
""")
    (trusted / "facade.py").write_text("""
from helpertools.effects import emit as send, legacy as old_send, chained as chain_send
from helpertools.helpers import pure as compute
""")
    (trusted / "contracts.py").write_text("""
from clauz3.spec import contract, effect
from helpertools.effects import emit, legacy, chained

@contract
def bounded(limit):
    return effect(emit, legacy, chained).all(lambda e: e.value <= limit)
""")
    return tmp_path, trusted, sentinel


@pytest.mark.parametrize(
    ("module", "name", "body"),
    [
        ("helpers", "pure", "output(pure(0))"),
        ("facade", "compute", "output(compute(0))"),
        ("helpers", "effectful", "effectful(1)"),
        ("helpers", "effectful_void", "effectful_void(1)"),
        # An unrelated helper can have exactly the same name as a boundary.
        ("helpers", "emit", "emit(1)"),
        # A bare deal.has() declares no effect and the prover inlines it.
        ("helpers", "unmarked", "output(unmarked(1))"),
    ],
)
def test_helper_is_incomplete_without_running_its_body_or_inventing_effects(
    layer: tuple[Path, Path, Path], module: str, name: str, body: str
) -> None:
    imports, trusted, sentinel = layer
    report = check_source(
        f"""
import clauz3
from helpertools.effects import emit as output
from helpertools.{module} import {name}
from helpertools.contracts import bounded
@clauz3.guarantee(bounded(1))
def main() -> None:
    {body}
""",
        import_roots=[imports],
        trusted_roots=[trusted],
        runs=1,
    )
    assert report.status == "incomplete", report
    assert not report.ok
    assert not report.runs
    assert "trusted-module helper helpertools.helpers." in report.reason
    assert "not a declared effect" in report.reason
    assert not sentinel.exists()


@pytest.mark.parametrize("name", ["send", "old_send", "chain_send"])
def test_source_declared_reexported_boundaries_still_replay(
    layer: tuple[Path, Path, Path], name: str
) -> None:
    imports, trusted, sentinel = layer
    report = check_source(
        f"""
import clauz3
from helpertools.facade import {name}
from helpertools.contracts import bounded
@clauz3.guarantee(bounded(1))
def main() -> None:
    {name}(1)
""",
        import_roots=[imports],
        trusted_roots=[trusted],
        runs=1,
    )
    assert report.ok, report
    assert len(report.runs) == 1
    assert f"helpertools.facade.{name}" in str(report.runs[0].effects)
    assert not sentinel.exists()

"""Regression test for chained-comparison handling in the program subset.

See https://github.com/cmungall/agent-deal/issues/43. Before the fix, the
Compare-node evaluator returned inside the first iteration of its
``for op, right_node in node.ops`` loop, so chained ``Compare`` nodes like
``0 < x <= 200`` silently dropped every comparator after the first. The
LIMS example surfaced this because ``@deal.pre(lambda ...: 0 < volume_ul <= 200)``
looked correct, lint-clean, and silently failed to enforce the upper bound.

These tests pin the fix in place by exercising chained comparisons in two
places they matter most: trusted preconditions (the original bug surface)
and agent-authored ``assert``s (the same evaluator).
"""

from pathlib import Path

import pytest

from clauz3.prover import prove_path

ROOT = Path(__file__).resolve().parents[1]
LIMS_ROOT = ROOT / "examples/lims"
LIMS_TRUSTED = LIMS_ROOT / "tools/lims/trusted"


@pytest.mark.parametrize(
    ("case_name", "ok"),
    [
        # 50 satisfies 0 < volume_ul <= 200
        ("only_plate_pass.py", True),
        # 500 violates the chained precondition's upper bound; the test
        # is the regression: before the fix this incorrectly proved.
        ("pipette_volume_precondition_fail.py", False),
    ],
)
def test_chained_precondition_via_lims_example(case_name: str, ok: bool) -> None:
    case = LIMS_ROOT / "cases" / case_name
    results = prove_path(case, trusted_roots=[LIMS_TRUSTED], import_roots=[LIMS_ROOT])
    assert len(results) == 1
    assert results[0].ok is ok


def test_chained_comparison_in_agent_assert(tmp_path: Path) -> None:
    """An agent-authored chained ``assert`` should enforce every comparator."""
    case = tmp_path / "chained_assert_fail.py"
    case.write_text(
        "# ruff: noqa: F821\n"
        "from tools.lims.trusted import contracts as lims\n"
        "from tools.lims.trusted.effects import pipette\n"
        "import clauz3\n"
        "\n"
        "@clauz3.guarantee(lims.only_plate('plate_42'))\n"
        "def main() -> None:\n"
        "    x = 250\n"
        "    assert 0 < x <= 200\n"
        "    pipette('plate_42', 'A1', 50, 'ATP')\n"
    )
    results = prove_path(case, trusted_roots=[LIMS_TRUSTED], import_roots=[LIMS_ROOT])
    assert len(results) == 1
    # Before the fix, the assert reduced to `0 < x` and proved. After the fix,
    # the upper bound `x <= 200` is also enforced and the assertion fails.
    assert results[0].ok is False


def test_single_comparator_unchanged(tmp_path: Path) -> None:
    """Single-pair comparisons still prove (no regression for the 1-pair path)."""
    case = tmp_path / "single_cmp_pass.py"
    case.write_text(
        "# ruff: noqa: F821\n"
        "from tools.lims.trusted import contracts as lims\n"
        "from tools.lims.trusted.effects import pipette\n"
        "import clauz3\n"
        "\n"
        "@clauz3.guarantee(lims.only_plate('plate_42'))\n"
        "def main() -> None:\n"
        "    x = 50\n"
        "    assert x > 0\n"
        "    pipette('plate_42', 'A1', x, 'ATP')\n"
    )
    results = prove_path(case, trusted_roots=[LIMS_TRUSTED], import_roots=[LIMS_ROOT])
    assert len(results) == 1
    assert results[0].ok is True

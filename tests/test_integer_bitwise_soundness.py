"""Regression tests for Python-sized integer bitwise semantics.

Python integers have arbitrary precision. The old solver implementation
silently truncated bitwise operands to 64-bit bit-vectors, which let a live
runtime branch look unreachable to the prover. Concrete evaluation is now exact
within a documented resource-safety budget and fails closed beyond it.
"""

from pathlib import Path

import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._proxies._int import (
    _BoundedConcreteIntEvaluator,
    _int_val_without_decimal_expansion,
)
from clauz3.prover import ProofResult, prove_text

ROOT = Path(__file__).resolve().parents[1]
HOME_ROOT = ROOT / "examples/home-automation"
TRUSTED_ROOT = HOME_ROOT / "tools/home/trusted"

HEADER = """\
import clauz3
from tools.home.trusted import contracts as home
from tools.home.trusted.effects import unlock_door
"""

OVERSIZED_HEX_LITERAL = "0x" + ("f" * 3750)
OVERSIZED_OCTAL_LITERAL = "0o" + ("7" * 5000)
OVERSIZED_BINARY_LITERAL = "0b1" + ("0" * 14999)
OVERSIZED_DECIMAL_LITERAL = "9" * 2000
TOO_WIDE_BINARY_LITERAL = "0b1" + ("0" * 100_000)
PINNED_LITERAL_VALUE = (
    (0x123456789ABCDEF << 14_000) | (0xFEDCBA987654321 << 7_000) | 0x10203040506070809
)
PINNED_LITERAL = hex(PINNED_LITERAL_VALUE)


def _literal_chunk_reconstruction(value: int) -> str:
    chunk_bits = 1024
    chunk_mask = (1 << chunk_bits) - 1
    chunks: list[int] = []
    while value:
        chunks.append(value & chunk_mask)
        value >>= chunk_bits
    expression = hex(chunks.pop())
    base = hex(1 << chunk_bits)
    while chunks:
        expression = f"({expression} * {base} + {hex(chunks.pop())})"
    return expression


def _prove(body: str) -> ProofResult:
    results = prove_text(
        HEADER + body,
        import_paths=[HOME_ROOT],
        trusted_roots=[TRUSTED_ROOT],
    )
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize(
    "condition",
    [
        "(18446744073709551616 & 18446744073709551616) != 0",
        "(18446744073709551616 | 0) != 0",
        "(18446744073709551616 ^ 0) != 0",
        "(1 << 64) != 0",
        "(18446744073709551616 >> 64) == 1",
        "(~18446744073709551616) == -18446744073709551617",
    ],
)
def test_wide_concrete_bitwise_branch_does_not_hide_effect(condition: str) -> None:
    result = _prove(f"""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    if {condition}:
        unlock_door("front")
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_symbolic_wide_shift_fails_closed() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main(value: int) -> None:
    if (value << 64) != 0:
        unlock_door("front")
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert "symbolic integer bitwise operation is unsupported" in str(
        result.proof.error
    )


@pytest.mark.parametrize(
    "expression",
    [
        "value & 18446744073709551616",
        "18446744073709551616 & value",
        "value | 0",
        "value ^ 0",
        "~value",
    ],
)
def test_symbolic_bitwise_operators_fail_closed(expression: str) -> None:
    result = _prove(f"""
@clauz3.guarantee(home.no_unlocks())
def main(value: int) -> None:
    if {expression}:
        unlock_door("front")
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert "symbolic integer bitwise operation is unsupported" in str(
        result.proof.error
    )


def test_small_concrete_bitwise_expressions_still_prove() -> None:
    result = _prove("""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    assert (6 & 3) == 2
    assert (4 | 1) == 5
    assert (7 ^ 3) == 4
    assert (3 << 2) == 12
    assert (12 >> 2) == 3
    assert ~3 == -4
    assert (((3 * 5) + 1) << (2 + 2)) == 256
    assert ((100 // 3) & 31) == 1
    assert ((100 % 33) | 2) == 3
    assert ((-5 // 2) & 7) == 5
    assert ((5 // -3) & 7) == 6
    assert ((-5 % 3) | 2) == 3
    assert ((5 % -3) & 7) == 7
    assert (-5 % -3) == -2
    assert ~True == -2
""")
    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize(
    ("expression", "error_fragment"),
    [
        pytest.param(
            f"{hex(1 << 4096)} & 1",
            "4096-bit safety limit",
            id="oversized-operand",
        ),
        pytest.param(
            f"~{hex((1 << 4096) - 1)}",
            "4096-bit safety limit",
            id="oversized-result",
        ),
        pytest.param(
            "1 << 4096",
            "4096-bit safety limit",
            id="oversized-left-shift-result",
        ),
        pytest.param(
            "1 << 1_000_000_000",
            "safety limit of 4096 positions",
            id="oversized-left-shift-count",
        ),
        pytest.param(
            "1 >> 4097",
            "safety limit of 4096 positions",
            id="oversized-right-shift-count",
        ),
    ],
)
def test_concrete_bitwise_safety_limit_fails_closed(
    expression: str,
    error_fragment: str,
) -> None:
    result = _prove(f"""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    if {expression}:
        unlock_door("front")
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert error_fragment in str(result.proof.error)


@pytest.mark.parametrize("operator", ["<<", ">>"])
def test_negative_shift_fails_closed(operator: str) -> None:
    result = _prove(f"""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    value = 1 {operator} -1
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert result.proof.description == "unsupported program body"
    assert "negative shift count" in str(result.proof.error)


@pytest.mark.parametrize(
    "expression",
    [
        "0 if True else (1 << -1)",
        "0 if True else (1 >> -1)",
        "flag and (1 << -1)",
        "flag and (1 >> -1)",
    ],
)
def test_suppressed_dead_negative_shift_cannot_hide_effect(expression: str) -> None:
    parameter = "flag: bool" if "flag" in expression else ""
    result = _prove(f"""
import deal

@deal.raises(ValueError)
@clauz3.guarantee(home.no_unlocks())
def main({parameter}) -> None:
    value = {expression}
    unlock_door("front")
""")
    if "flag" in expression:
        # This operand can execute, so the unsupported shift still refuses.
        assert result.proof.conclusion is Conclusion.SKIP
        assert result.proof.description == "unsupported program body"
        assert "negative shift count" in str(result.proof.error)
    else:
        # A statically skipped conditional arm cannot hide the later effect.
        assert result.proof.conclusion is Conclusion.FAIL
        assert result.proof.description == "guarantee"


def test_compact_repeated_squaring_fails_without_general_simplification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_general_simplification(*args: object, **kwargs: object) -> None:
        pytest.fail("bitwise concrete evaluation called unbounded z3.simplify")

    monkeypatch.setattr(z3, "simplify", reject_general_simplification)
    squares = "\n    ".join(["shift = shift * shift"] * 32)
    result = _prove(f"""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    shift = 2
    {squares}
    if 1 >> shift:
        unlock_door("front")
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert result.proof.description == "unsupported program body"
    assert "shift count" in str(result.proof.error)
    assert "4096-bit safety limit" in str(result.proof.error)


@pytest.mark.parametrize(
    "assertion",
    [
        "(2 & True) == 0",
        "(2 | False) == 2",
        "(2 ^ True) == 3",
        "(2 << True) == 4",
        "(2 >> True) == 1",
        "(True & 2) == 0",
        "(False | 2) == 2",
        "(True ^ 2) == 3",
        "(True << 2) == 4",
        "(True >> 2) == 0",
        "(True & False) == False",
        "(True | False) == True",
        "(True ^ True) == False",
        "(True << False) == 1",
        "(True >> True) == 0",
    ],
)
def test_concrete_bool_bitwise_coercion_matches_python(assertion: str) -> None:
    result = _prove(f"""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    assert {assertion}
""")
    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize(
    "condition",
    [
        "(2 & True) == 0",
        "(2 | False) == 2",
        "(2 ^ True) == 3",
        "(2 << True) == 4",
        "(2 >> True) == 1",
        "(True & 2) == 0",
        "(False | 2) == 2",
        "(True ^ 2) == 3",
        "(True << 2) == 4",
        "(True >> 2) == 0",
    ],
)
def test_deal_raises_does_not_hide_valid_bool_bitwise_effect(
    condition: str,
) -> None:
    result = _prove(f"""
import deal

@deal.raises(TypeError)
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    if {condition}:
        unlock_door("front")
""")
    assert result.proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize(
    "expression",
    [
        "flag << False",
        "flag >> True",
        "1 & flag",
        "1 | flag",
        "1 ^ flag",
        "1 << flag",
        "1 >> flag",
    ],
)
def test_symbolic_bool_bitwise_operators_fail_closed(expression: str) -> None:
    result = _prove(f"""
@clauz3.guarantee(home.no_unlocks())
def main(flag: bool) -> None:
    if {expression}:
        unlock_door("front")
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert "symbolic integer bitwise operation is unsupported" in str(
        result.proof.error
    )


@pytest.mark.parametrize(
    "expression",
    [
        "flag & True",
        "flag | False",
        "flag ^ True",
        "True & flag",
        "False | flag",
        "True ^ flag",
    ],
)
def test_symbolic_bool_only_bitwise_operators_are_exact(expression: str) -> None:
    result = _prove(f"""
@clauz3.guarantee(home.no_unlocks())
def main(flag: bool) -> None:
    if {expression}:
        unlock_door("front")
""")
    assert result.proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize(
    "literal",
    [
        pytest.param(OVERSIZED_HEX_LITERAL, id="hex"),
        pytest.param(OVERSIZED_OCTAL_LITERAL, id="octal"),
        pytest.param(OVERSIZED_BINARY_LITERAL, id="binary"),
        pytest.param(OVERSIZED_DECIMAL_LITERAL, id="decimal"),
        pytest.param(f"-{OVERSIZED_HEX_LITERAL}", id="negative-hex"),
        pytest.param(f"-{OVERSIZED_OCTAL_LITERAL}", id="negative-octal"),
        pytest.param(f"-{OVERSIZED_BINARY_LITERAL}", id="negative-binary"),
        pytest.param(f"-{OVERSIZED_DECIMAL_LITERAL}", id="negative-decimal"),
    ],
)
def test_oversized_based_literal_fails_closed_without_escaping(literal: str) -> None:
    result = _prove(f"""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    if ({literal}) & 1:
        unlock_door("front")
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert result.proof.description == "unsupported program body"
    assert "left operand" in str(result.proof.error)
    assert "4096-bit safety limit" in str(result.proof.error)


def test_oversized_literal_remains_exact_outside_bitwise_evaluation() -> None:
    result = _prove(f"""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    assert {OVERSIZED_HEX_LITERAL} > 0
""")
    assert result.proof.conclusion is Conclusion.OK


def test_chunked_literal_encoding_preserves_the_exact_value_and_sign() -> None:
    reconstruction = _literal_chunk_reconstruction(PINNED_LITERAL_VALUE)
    residue = PINNED_LITERAL_VALUE % 65_521
    result = _prove(f"""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    assert {PINNED_LITERAL} == {reconstruction}
    assert -{PINNED_LITERAL} == -({reconstruction})
    assert {PINNED_LITERAL} % 65_521 == {residue}
""")
    assert result.proof.conclusion is Conclusion.OK

    positive = _int_val_without_decimal_expansion(PINNED_LITERAL_VALUE, ctx=None)
    negative = _int_val_without_decimal_expansion(-PINNED_LITERAL_VALUE, ctx=None)
    assert z3.eq(negative, -positive)


def test_extreme_integer_literal_fails_before_chunk_construction() -> None:
    result = _prove(f"""
@clauz3.guarantee(home.no_unlocks())
def main() -> None:
    assert {TOO_WIDE_BINARY_LITERAL} > 0
""")
    assert result.proof.conclusion is Conclusion.SKIP
    assert "integer literal exceeds the 100000-bit safety limit" in str(
        result.proof.error
    )


def test_nary_distinct_is_not_misread_as_a_binary_comparison() -> None:
    expression = z3.If(
        z3.Distinct(z3.IntVal(1), z3.IntVal(2), z3.IntVal(1)),
        z3.IntVal(1),
        z3.IntVal(0),
    )
    evaluator = _BoundedConcreteIntEvaluator(op="&", role="left operand")

    assert evaluator.evaluate(expression) is None


@pytest.mark.parametrize(
    "expression",
    [
        z3.Var(0, z3.IntSort()),
        z3.If(
            z3.ForAll([z3.Int("index")], z3.Int("index") >= 0),
            z3.IntVal(1),
            z3.IntVal(0),
        ),
    ],
)
def test_non_application_nodes_fail_closed_without_z3_exception(
    expression: z3.ArithRef,
) -> None:
    evaluator = _BoundedConcreteIntEvaluator(op="&", role="left operand")

    assert evaluator.evaluate(expression) is None

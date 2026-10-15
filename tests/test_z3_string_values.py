"""The Z3 boundary preserves Python code points in either solver context."""

import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import StrSort
from clauz3._vendor.deal_solver._strings import (
    is_string_expr,
    is_string_sort,
    string_domain,
    string_sort,
    string_val,
    string_value,
)
from clauz3.prover import prove_text


@pytest.mark.parametrize("custom_context", [False, True])
@pytest.mark.parametrize(
    "value",
    [
        "",
        r"\u{41}",
        "a\x00b",
        "Ω😀",
        "\ud800",
        "\U0002ffff",
        "\U00030000",
        "\U0010ffff",
    ],
)
def test_string_code_points_round_trip(value: str, custom_context: bool) -> None:
    context = z3.Context() if custom_context else z3.main_ctx()
    literal = string_val(value, ctx=context)

    assert literal.ctx is context
    assert string_value(literal) == value
    assert is_string_expr(literal)
    assert is_string_sort(literal.sort())
    assert z3.simplify(z3.Length(literal)).as_long() == len(value)
    for index, char in enumerate(value):
        piece = z3.SubSeq(literal, index, 1)
        assert string_value(piece) == char
        assert z3.simplify(z3.BV2Int(literal[index], is_signed=False)).as_long() == ord(
            char
        )


def test_concatenated_code_points_decode_as_python_text() -> None:
    expression = z3.Concat(string_val("\\"), string_val("u{41}"), string_val("Ω"))

    assert string_value(expression) == r"\u{41}" + "Ω"


def test_symbolic_string_cannot_be_decoded_as_literal() -> None:
    with pytest.raises(UnsupportedError, match="expected a concrete string"):
        string_value(z3.Const("unknown", string_sort()))


@pytest.mark.parametrize("point", [0x110000, 0x1FFFFF])
def test_non_python_code_point_cannot_be_decoded(point: int) -> None:
    invalid = z3.Unit(z3.BitVecVal(point, 21))
    with pytest.raises(UnsupportedError, match="exceeds Python Unicode range"):
        string_value(invalid)

    solver = z3.Solver()
    solver.add(string_domain(invalid))
    assert solver.check() == z3.unsat


def test_string_sort_remains_distinct_from_integer_sequences() -> None:
    sequence = z3.Unit(z3.IntVal(65))
    assert not is_string_expr(sequence)
    assert not is_string_sort(sequence.sort())
    with pytest.raises(UnsupportedError, match="expected a concrete string"):
        string_value(sequence)


@pytest.mark.parametrize("kind", ["literal", "symbolic", "integer_sequence"])
def test_string_proxy_refuses_incompatible_sorts(kind: str) -> None:
    expr = {
        "literal": z3.StringVal("text"),
        "symbolic": z3.String("text"),
        "integer_sequence": z3.Unit(z3.IntVal(65)),
    }[kind]
    with pytest.raises(UnsupportedError, match="use string_val or string_sort"):
        StrSort(expr)


def test_native_string_custom_model_reports_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def native_string_var(*, name: str, ctx: z3.Context) -> StrSort:
        return StrSort(z3.String(name, ctx=ctx))

    monkeypatch.setattr(StrSort, "var", staticmethod(native_string_var))
    (result,) = prove_text("def main(text: str) -> None:\n    assert text == text\n")
    assert result.proof.conclusion == Conclusion.SKIP
    assert result.proof.description == "failed to interpret function arguments"
    assert isinstance(result.proof.error, UnsupportedError)
    assert "use string_val or string_sort" in str(result.proof.error)

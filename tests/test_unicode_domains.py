"""External values span Python Unicode, including nested container contents."""

import astroid
import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._proxies import DictSort, ProxySort, SetSort
from clauz3._vendor.deal_solver._proxies._func import _materialize_trusted_return
from clauz3._vendor.deal_solver._proxies._row import QueryResultSort
from clauz3._vendor.deal_solver._strings import string_val
from clauz3.prover import prove_text


@pytest.mark.parametrize(
    ("annotation", "item", "guard"),
    [
        ("str", "value", "len(value) > 0"),
        ("list[str]", "value[0]", "len(value) > 0 and len(value[0]) > 0"),
        ("tuple[str, ...]", "value[0]", "len(value) > 0 and len(value[0]) > 0"),
        (
            "list[list[str]]",
            "value[0][0]",
            "len(value) > 0 and len(value[0]) > 0 and len(value[0][0]) > 0",
        ),
        (
            "dict[int, str]",
            "value[1]",
            "1 in value and len(value[1]) > 0",
        ),
        (
            "dict[str, str]",
            "value['key']",
            "'key' in value and len(value['key']) > 0",
        ),
        (
            "dict[str, tuple[str, ...]]",
            "value['key'][0]",
            "'key' in value and len(value['key']) > 0 and len(value['key'][0]) > 0",
        ),
    ],
    ids=["str", "list", "tuple", "nested-list", "int-key", "str-key", "dict-tuple"],
)
@pytest.mark.parametrize(
    "upper", [0x10FFFF, 0x2FFFF], ids=["python-range", "old-range"]
)
def test_argument_strings_have_the_full_python_character_domain(
    annotation: str, item: str, guard: str, upper: int
) -> None:
    source = f"def main(value: {annotation}) -> None:\n"
    indentation = "    "
    # This branch is based before the independent Boolean short-circuit fix.
    # Nested statements keep each later indexing operation behind its bound.
    for condition in guard.split(" and "):
        source += f"{indentation}if {condition}:\n"
        indentation += "    "
    source += f"{indentation}assert ord(({item})[0]) <= {upper}\n"
    if upper == 0x2FFFF:
        # Supply a real Python counterexample without asking the quantified
        # nested-sequence solver to discover an arbitrary finite model.
        high = ascii("\U0010ffff")
        witnesses = {
            "str": high,
            "list[str]": f"[{high}]",
            "tuple[str, ...]": f"({high},)",
            "list[list[str]]": f"[[{high}]]",
            "dict[int, str]": f"{{1: {high}}}",
            "dict[str, str]": f"{{'key': {high}}}",
            "dict[str, tuple[str, ...]]": f"{{'key': ({high},)}}",
        }
        source = (
            "import deal\n"
            f"@deal.pre(lambda value: value == {witnesses[annotation]})\n" + source
        )

    results = prove_text(source)

    expected = Conclusion.OK if upper == 0x10FFFF else Conclusion.FAIL
    assert [result.proof.conclusion for result in results] == [expected], [
        str(result.proof) for result in results
    ]


def _external(annotation: str) -> tuple[ProxySort, Context]:
    function = astroid.parse(f"def fetch() -> {annotation}:\n    pass\n").body[0]
    assert isinstance(function, astroid.FunctionDef)
    context = Context.make_empty(get_contracts=lambda _: iter(()))
    value = _materialize_trusted_return(node=function, bound_args={}, ctx=context)
    assert value is not None
    return value, context


def _satisfiable(context: Context, *constraints: z3.BoolRef) -> z3.CheckSatResult:
    solver = z3.Solver()
    solver.set(timeout=3000)
    solver.add(*(condition.expr for condition in context.given), *constraints)
    return solver.check()


def _character(point: int) -> z3.SeqRef:
    # Constructing an out-of-Python code point is intentional here: it tests
    # symbolic input domains without passing through the literal validator.
    return z3.Unit(z3.BitVecVal(point, 21))


@pytest.mark.parametrize("point", [0, 0xD800, 0x30000, 0x10FFFF, 0x110000])
@pytest.mark.parametrize("annotation", ["str", "list[str]", "tuple[str, ...]"])
def test_trusted_scalar_and_sequence_results_have_exact_character_bounds(
    annotation: str, point: int
) -> None:
    value, context = _external(annotation)
    concrete = _character(point)
    if annotation != "str":
        concrete = z3.Unit(concrete)

    if annotation != "str" and point > 0x10FFFF:
        # Observe an in-bounds element directly. Z3's quantified nested-seq
        # equality solver is incomplete even for a one-element invalid value.
        result = _satisfiable(
            context,
            z3.Length(value.expr) == 1,
            z3.Length(value.expr[0]) == 1,
            value.expr[0][0] == z3.BitVecVal(point, 21),
        )
    else:
        result = _satisfiable(context, value.expr == concrete)

    assert result == (z3.sat if point <= 0x10FFFF else z3.unsat)


@pytest.mark.parametrize("invalid_key", [False, True], ids=["value", "key"])
def test_present_dictionary_entries_enforce_both_key_and_value_domains(
    invalid_key: bool,
) -> None:
    value, context = _external("dict[str, str]")
    assert isinstance(value, DictSort)
    valid = string_val("\U0010ffff")
    invalid = _character(0x110000)
    key = invalid if invalid_key else valid
    item = valid if invalid_key else invalid
    present = z3.Select(value.expr, key)

    assert (
        _satisfiable(
            context,
            value.item_sort.exists(present),
            value.item_sort.value(present) == item,
        )
        == z3.unsat
    )


def test_absent_dictionary_values_do_not_restrict_the_domain() -> None:
    value, context = _external("dict[str, str]")
    assert isinstance(value, DictSort)
    invalid = _character(0x110000)
    absent = value.item_sort.new(z3.BoolVal(False), invalid)
    empty = z3.K(invalid.sort(), absent)
    valid = string_val("\U0010ffff")
    populated = z3.Store(empty, valid, value.item_sort.new(z3.BoolVal(True), valid))

    assert _satisfiable(context, value.expr == empty) == z3.sat
    assert _satisfiable(context, value.expr == populated) == z3.sat


def test_nested_dictionary_values_constrain_only_present_items() -> None:
    value, context = _external("dict[str, list[str]]")
    assert isinstance(value, DictSort)
    key = string_val("key")
    present = z3.Select(value.expr, key)
    items = value.item_sort.value(present)

    assert (
        _satisfiable(
            context,
            value.item_sort.exists(present),
            z3.Length(items) == 1,
            z3.Length(items[0]) == 1,
            items[0][0] == z3.BitVecVal(0x110000, 21),
        )
        == z3.unsat
    )


def test_set_members_are_valid_but_nonmembers_need_no_character_constraint() -> None:
    value, context = _external("set[str]")
    assert isinstance(value, SetSort)
    invalid = _character(0x110000)
    valid = string_val("\U0010ffff")
    empty = z3.EmptySet(valid.sort())

    assert _satisfiable(context, value.expr == empty) == z3.sat
    assert _satisfiable(context, value.expr == z3.SetAdd(empty, valid)) == z3.sat
    assert _satisfiable(context, z3.IsMember(invalid, value.expr)) == z3.unsat


@pytest.mark.parametrize("point", [0xD800, 0x30000, 0x10FFFF, 0x110000])
def test_query_rows_constrain_present_string_fields(point: int) -> None:
    module = astroid.parse(
        "import clauz3\n"
        "class Result(clauz3.Row):\n"
        "    text: str\n"
        "    count: int\n"
        "def fetch() -> list[Result]:\n"
        "    pass\n"
    )
    function = module.body[-1]
    assert isinstance(function, astroid.FunctionDef)
    context = Context.make_empty(get_contracts=lambda _: iter(()))
    value = _materialize_trusted_return(node=function, bound_args={}, ctx=context)
    assert isinstance(value, QueryResultSort)
    row = value.at(z3.IntVal(0), ctx=context)
    text = row.field("text", ctx=context)

    result = _satisfiable(
        context, value.length_expr == 1, text.expr == _character(point)
    )

    assert result == (z3.sat if point <= 0x10FFFF else z3.unsat)
    # The same unconstrained array cell is not a row when the query is empty.
    assert (
        _satisfiable(context, value.length_expr == 0, text.expr == _character(point))
        == z3.sat
    )

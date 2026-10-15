from __future__ import annotations

import math

import pytest
import z3

from clauz3 import Row
from clauz3._vendor.deal_solver._context import FactInfo
from clauz3._vendor.deal_solver._context._quantifier import Quantifier
from clauz3._vendor.deal_solver._domains import value_domain
from clauz3._vendor.deal_solver._proxies import (
    BoolSort,
    DictSort,
    IntSort,
    ListSort,
    ProxySort,
    SetSort,
    StrSort,
    UntypedDictSort,
    UntypedListSort,
    UntypedSetSort,
    UntypedVarTupleSort,
)
from clauz3._vendor.deal_solver._proxies._float import FPSort
from clauz3._vendor.deal_solver._proxies._row import (
    QueryResultSort,
    RowClassSort,
    RowSort,
    z3_datatype_for_row,
)
from clauz3._vendor.deal_solver._strings import string_sort, string_val
from clauz3.differential_inputs import UnsupportedInputs, concretize, model_cases
from clauz3.prover import ProofObservation


class GeneratedRow(Row):
    text: str
    count: int
    enabled: bool


def _query(name: str = "query") -> QueryResultSort:
    return QueryResultSort(
        row_schema=GeneratedRow,
        array_expr=z3.Array(name, z3.IntSort(), z3_datatype_for_row(GeneratedRow)),
        length_expr=z3.Int(name + "_length"),
        source=("read", {}),
    )


def _fact(
    name: str,
    result: ProxySort | None = None,
    *,
    args: dict[str, ProxySort] | None = None,
    condition: z3.BoolRef | None = None,
    quantifiers: tuple[Quantifier, ...] = (),
) -> FactInfo:
    return FactInfo(
        name=name,
        markers=(),
        args=args or {},
        cond=BoolSort(z3.BoolVal(True) if condition is None else condition),
        quantifiers=quantifiers,
        result=result,
        qualified_name=f"trusted.effects.{name}",
    )


def _observation(
    *facts: FactInfo,
    assumptions: z3.BoolRef | None = None,
    arguments: dict[str, ProxySort] | None = None,
) -> ProofObservation:
    return ProofObservation(
        assumptions=z3.BoolVal(True) if assumptions is None else assumptions,
        arguments=arguments or {},
        effects=facts,
        contracts=(),
        guarantees=(),
    )


def test_generated_rows_are_nonempty_and_satisfy_assumptions() -> None:
    query = _query()
    datatype = query.array_expr.sort().range()
    index = z3.Int("row_index")
    row = z3.Select(query.array_expr, index)
    assumptions = z3.And(
        query.length_expr >= 0,
        z3.ForAll(
            index,
            z3.Implies(
                z3.And(index >= 0, index < query.length_expr),
                z3.And(datatype.accessor(0, 1)(row) >= 7, datatype.accessor(0, 2)(row)),
            ),
        ),
    )
    cases = list(
        model_cases(_observation(_fact("read", query), assumptions=assumptions))
    )
    rows_by_case = [case.effects[0].result for case in cases]
    assert [len(rows) for rows in rows_by_case[:3] if isinstance(rows, list)] == [
        1,
        0,
        2,
    ]
    for case, rows in zip(cases, rows_by_case, strict=True):
        assert isinstance(rows, list)
        assert case.model.eval(query.length_expr).as_long() == len(rows)
        assert all(isinstance(row, GeneratedRow) for row in rows)
        assert all(row.count >= 7 and row.enabled for row in rows)


def test_projection_exhaustion_ignores_invisible_query_array_tail() -> None:
    query = _query()
    cases = list(
        model_cases(
            _observation(
                _fact("read", query), arguments={"flag": BoolSort(z3.Bool("f"))}
            ),
            max_rows=0,
            runs=10,
        )
    )
    assert len(cases) == 2
    assert {case.arguments["flag"] for case in cases} == {True, False}
    assert all(case.effects[0].result == [] for case in cases)


def test_deterministic_program_gets_one_run() -> None:
    cases = list(model_cases(_observation(_fact("write")), runs=20))
    assert len(cases) == 1
    assert cases[0].effects[0].result is None


def test_repeated_reads_keep_distinct_returns() -> None:
    first, second = IntSort(z3.Int("first")), IntSort(z3.Int("second"))
    cases = list(
        model_cases(
            _observation(
                _fact("read", first),
                _fact("read", second),
                assumptions=first.expr != second.expr,
            ),
            runs=4,
        )
    )
    assert len(cases) == 4
    assert all(case.effects[0].result != case.effects[1].result for case in cases)
    assert len({tuple(effect.result for effect in case.effects) for case in cases}) == 4


def test_nan_return_projection_exhausts_after_one_model() -> None:
    result = FPSort(z3.FP("nan_result", z3.Float64()))
    cases = list(
        model_cases(
            _observation(_fact("read", result), assumptions=z3.fpIsNaN(result.expr)),
            runs=10,
        )
    )
    # Blocking uses structural Z3 equality, under which NaN equals itself.
    assert len(cases) == 1
    value = cases[0].effects[0].result
    assert isinstance(value, float)
    assert math.isnan(value)


def test_zero_return_projection_samples_both_signs_then_exhausts() -> None:
    result = FPSort(z3.FP("zero_result", z3.Float64()))
    cases = list(
        model_cases(
            _observation(_fact("read", result), assumptions=z3.fpIsZero(result.expr)),
            runs=10,
        )
    )
    # Structural equality distinguishes signed zero despite Python's equality.
    assert len(cases) == 2
    signs = set()
    for case in cases:
        value = case.effects[0].result
        assert isinstance(value, float)
        assert value == 0.0
        signs.add(math.copysign(1.0, value))
    assert signs == {-1.0, 1.0}


def test_inactive_reads_do_not_create_duplicate_inputs() -> None:
    cases = list(
        model_cases(
            _observation(
                _fact("read", IntSort(z3.Int("unused")), condition=z3.BoolVal(False))
            )
        )
    )
    assert len(cases) == 1
    assert cases[0].effects == ()


def test_quantified_effect_arguments_are_expanded_from_real_rows() -> None:
    query = _query()
    index = z3.Int("effect_index")
    frame = Quantifier(index, query, z3.IntVal(0), query.length_expr)
    row = RowSort(schema=GeneratedRow, expr=z3.Select(query.array_expr, index))
    cases = list(
        model_cases(
            _observation(
                _fact("read", query),
                _fact("write", args={"row": row}, quantifiers=(frame,)),
                assumptions=query.length_expr == 2,
            ),
            runs=2,
        )
    )
    assert len(cases) == 2
    for case in cases:
        assert len(case.effects) == 3
        assert [effect.arguments["row"] for effect in case.effects[1:]] == (
            case.effects[0].result
        )


def test_quantified_query_returns_are_explicitly_unsupported() -> None:

    query = _query()
    frame = Quantifier(z3.Int("i"), query, z3.IntVal(0), query.length_expr)
    with pytest.raises(UnsupportedInputs, match="container-returning.*quantified"):
        list(
            model_cases(
                _observation(_fact("read", _query("nested"), quantifiers=(frame,)))
            )
        )


def test_quantified_scalar_returns_use_correlated_values_and_report_limitation() -> (
    None
):
    query = _query()
    index = z3.Int("iteration")
    frame = Quantifier(index, query, z3.IntVal(0), query.length_expr)
    cases = list(
        model_cases(
            _observation(
                _fact("query", query),
                _fact(
                    "read",
                    StrSort(z3.Const("shared_result", string_sort())),
                    quantifiers=(frame,),
                ),
                assumptions=query.length_expr == 2,
            ),
            runs=4,
        )
    )
    assert len(cases) == 4
    for case in cases:
        assert len(case.effects) == 3
        assert case.effects[1].result == case.effects[2].result
        assert case.limitations == (
            "trusted.effects.read: quantified returns share "
            "a symbolic value across iterations",
        )


def test_quantified_return_projection_substitutes_loop_index() -> None:
    query = _query()
    index = z3.Int("iteration")
    frame = Quantifier(index, query, z3.IntVal(0), query.length_expr)
    observation = _observation(
        # Use a fixed query so only the read's Boolean input can vary.
        _fact(
            "read",
            BoolSort(z3.Or(z3.Bool("read_value"), index == 0)),
            quantifiers=(frame,),
        ),
        assumptions=query.length_expr == 2,
    )
    cases = list(model_cases(observation, runs=10))
    assert len(cases) == 2
    assert all(not case.limitations for case in cases)
    assert {tuple(effect.result for effect in case.effects) for case in cases} == {
        (True, True),
        (True, False),
    }


def test_repeated_returning_sites_interleave_within_each_iteration() -> None:
    query = _query()
    index = z3.Int("iteration")
    frame = Quantifier(index, query, z3.IntVal(0), query.length_expr)
    first, second = IntSort(z3.Int("first_result")), IntSort(z3.Int("second_result"))
    observation = _observation(
        _fact("query", query),
        _fact("read", first, quantifiers=(frame,)),
        _fact(
            "write",
            args={"value": first},
            condition=first.expr == 1,
            quantifiers=(frame,),
        ),
        _fact("read", second, quantifiers=(frame,)),
        _fact(
            "write",
            args={"value": second},
            condition=second.expr == 2,
            quantifiers=(frame,),
        ),
        assumptions=z3.And(query.length_expr == 2, first.expr == 1, second.expr == 2),
    )
    case = next(model_cases(observation))
    assert [effect.fact_index for effect in case.effects] == [0, 1, 2, 3, 4, 1, 2, 3, 4]
    assert [effect.result for effect in case.effects if effect.name == "read"] == [
        1,
        2,
        1,
        2,
    ]
    assert [
        effect.arguments["value"] for effect in case.effects if effect.name == "write"
    ] == [1, 2, 1, 2]


def test_nested_effect_sites_reconstruct_iteration_then_source_order() -> None:
    query = _query()
    outer_index, inner_index = z3.Ints("outer_index inner_index")
    outer = Quantifier(outer_index, query, z3.IntVal(0), query.length_expr)
    inner = Quantifier(inner_index, query, z3.IntVal(0), query.length_expr)
    observation = _observation(
        _fact("query", query),
        _fact("read", IntSort(z3.IntVal(0)), quantifiers=(outer,)),
        # An inactive first site must still establish the nested loop's
        # position relative to the later outer-loop site.
        _fact("read", condition=z3.BoolVal(False), quantifiers=(outer, inner)),
        _fact("read", IntSort(z3.IntVal(1)), quantifiers=(outer, inner)),
        _fact("read", IntSort(z3.IntVal(2)), quantifiers=(outer, inner)),
        _fact("read", IntSort(z3.IntVal(3)), quantifiers=(outer,)),
        _fact("read", IntSort(z3.IntVal(4))),
        assumptions=query.length_expr == 2,
    )
    case = next(model_cases(observation))
    assert [effect.result for effect in case.effects if effect.name == "read"] == [
        0,
        1,
        2,
        1,
        2,
        3,
        0,
        1,
        2,
        1,
        2,
        3,
        4,
    ]


def test_unicode_decode_preserves_codepoints_and_literal_backslashes() -> None:
    text = "é😀\U00030000\U0010ffff\ud800\x00\\u{e9}"
    expr = string_val(text)
    solver = z3.Solver()
    assert solver.check() == z3.sat
    assert concretize(StrSort(expr), solver.model()) == text


def test_nonempty_precondition_overrides_empty_size_preference() -> None:
    query = _query()
    cases = list(
        model_cases(
            _observation(_fact("read", query), assumptions=query.length_expr > 0),
            runs=3,
        )
    )
    assert len(cases) == 3
    assert all(
        isinstance(c.effects[0].result, list) and c.effects[0].result for c in cases
    )


def test_effect_arguments_support_containers_without_applying_input_size_limit() -> (
    None
):
    key = StrSort(string_val(""))
    item = IntSort(z3.IntVal(0))
    sequence = ListSort(
        expr=z3.Concat(*[z3.Unit(z3.IntVal(index)) for index in range(5)]),
        subtypes=(item.factory,),
    )
    dictionary = DictSort.make_empty(key, sequence)
    dictionary.expr = z3.Store(
        dictionary.expr,
        string_val("numbers"),
        dictionary.item_sort.new(z3.BoolVal(True), sequence.expr),
    )
    set_value = SetSort(
        expr=z3.SetAdd(z3.EmptySet(z3.IntSort()), z3.IntVal(7)),
        subtypes=(item.factory,),
    )
    args = {
        "dictionary": dictionary,
        "numbers": sequence,
        "set": set_value,
        "empty_dict": UntypedDictSort(),
        "empty_list": UntypedListSort(),
        "empty_tuple": UntypedVarTupleSort(),
        "empty_set": UntypedSetSort(),
        "schema": RowClassSort(schema=GeneratedRow),
    }
    cases = list(model_cases(_observation(_fact("write", args=args)), max_rows=1))
    assert len(cases) == 1
    assert cases[0].effects[0].arguments == {
        "dictionary": {"numbers": [0, 1, 2, 3, 4]},
        "numbers": [0, 1, 2, 3, 4],
        "set": {7},
        "empty_dict": {},
        "empty_list": [],
        "empty_tuple": (),
        "empty_set": set(),
        "schema": GeneratedRow,
    }


def test_scalar_postcondition_keeps_other_fields_varied() -> None:
    query = _query()
    datatype = query.array_expr.sort().range()
    row = z3.Select(query.array_expr, 0)
    cases = list(
        model_cases(
            _observation(
                _fact("read", query),
                assumptions=z3.And(
                    query.length_expr == 1, datatype.accessor(0, 1)(row) >= 99
                ),
            ),
            runs=4,
        )
    )
    rows = [case.effects[0].result[0] for case in cases]  # type: ignore[index]
    assert {row.enabled for row in rows} == {True, False}
    assert {row.text for row in rows} >= {"é", "😀"}
    assert all(row.count >= 99 for row in rows)


def test_string_sequence_returns_generate_distinct_nonempty_models() -> None:
    strings = ListSort(
        expr=z3.Const("paths", z3.SeqSort(string_sort())),
        subtypes=(StrSort(string_val("")).factory,),
    )
    cases = list(
        model_cases(
            _observation(_fact("find", strings), assumptions=value_domain(strings)),
            runs=8,
        )
    )
    assert len(cases) == 8
    paths = [case.effects[0].result for case in cases]
    assert [len(value) for value in paths[:3] if isinstance(value, list)] == [1, 0, 2]
    assert len({tuple(value) for value in paths if isinstance(value, list)}) == 8
    assert any("é" in value for value in paths if isinstance(value, list))


def test_string_sequence_encoding_preserves_proof_assumptions() -> None:
    strings = ListSort(
        expr=z3.Const("paths", z3.SeqSort(string_sort())),
        subtypes=(StrSort(string_val("")).factory,),
    )
    required = "/required/path"
    cases = list(
        model_cases(
            _observation(
                _fact("find", strings),
                assumptions=z3.And(
                    z3.Length(strings.expr) > 0,
                    strings.expr[0] == string_val(required),
                ),
            ),
            runs=8,
        )
    )
    assert len(cases) == 8
    assert all(
        isinstance(case.effects[0].result, list)
        and case.effects[0].result[0] == required
        for case in cases
    )


@pytest.mark.parametrize("text", ["\U00030000", "\U0010ffff", "\ud800"])
def test_high_unicode_trusted_returns_and_rows_are_sampled_exactly(text: str) -> None:
    query = _query()
    returned = StrSort(z3.Const("returned_text", string_sort()))
    row = z3.Select(query.array_expr, 0)
    field = query.array_expr.sort().range().accessor(0, 0)(row)
    observation = _observation(
        _fact("read_text", returned),
        _fact("read_rows", query),
        assumptions=z3.And(
            returned.expr == string_val(text),
            query.length_expr == 1,
            field == returned.expr,
        ),
    )
    case = next(model_cases(observation, runs=1))
    assert case.effects[0].result == text
    rows = case.effects[1].result
    assert isinstance(rows, list)
    assert rows[0].text == text


def test_unconstrained_string_sampling_includes_full_unicode_edges() -> None:
    returned = StrSort(z3.Const("edge_text", string_sort()))
    cases = list(model_cases(_observation(_fact("read_text", returned)), runs=7))
    assert {case.effects[0].result for case in cases} >= {
        "\U00030000",
        "\U0010ffff",
        "\ud800",
    }

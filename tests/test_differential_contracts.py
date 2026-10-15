"""The differential oracle checks traces with Python contract semantics."""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest
import z3

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._proxies import types
from clauz3._vendor.deal_solver._strings import string_sort
from clauz3.differential_contracts import (
    ContractEvaluationError,
    EffectCall,
    check_contract,
)
from clauz3.fluent import effect as fluent_effect
from clauz3.fluent import fluent
from clauz3.row import Row
from clauz3.spec import (
    AllSpec,
    ComparisonSpec,
    ContractSpec,
    LambdaSpec,
    effect,
    no_guarantees,
)


def send(addr: str, msg: str = "default", amount: int = 1) -> None:
    raise AssertionError("the oracle must not call trusted effects")


def read() -> list[Row]:
    raise AssertionError("the oracle must not call trusted effects")


SEND = effect(send)
SIGNATURES = {
    "trusted.send": inspect.signature(send),
    "trusted.read": inspect.signature(read),
}


def calls(*addresses: str) -> list[EffectCall]:
    return [("trusted.send", (addr,), {}) for addr in addresses]


def test_all_binds_positional_keyword_and_default_arguments() -> None:
    spec = SEND.all(lambda e: e.addr == "bob" and e.msg == "default")
    assert check_contract(spec, calls("bob"), SIGNATURES)
    assert check_contract(spec, [("trusted.send", (), {"addr": "bob"})], SIGNATURES)
    assert not check_contract(spec, calls("eve"), SIGNATURES)
    assert not check_contract(
        spec, [("trusted.send", ("bob", "other"), {})], SIGNATURES
    )


def test_filtered_all_and_empty_ignore_other_relations() -> None:
    spec = SEND.where(lambda e: e.msg == "private").all(lambda e: e.addr == "bob")
    assert check_contract(spec, calls("eve"), SIGNATURES)
    assert not check_contract(
        spec, [("trusted.send", ("eve", "private"), {})], SIGNATURES
    )
    forbidden = SEND.where(lambda e: e.addr == "eve").empty()
    assert check_contract(forbidden, [("trusted.read", (), {})], SIGNATURES)
    assert not check_contract(forbidden, calls("eve"), SIGNATURES)
    assert not check_contract(effect().empty(), [("trusted.read", (), {})], SIGNATURES)


def test_distinct_checks_repeated_calls_and_unhashable_keys() -> None:
    distinct = SEND.distinct(lambda e: [e.addr, e.msg])
    assert check_contract(distinct, calls("bob", "eve"), SIGNATURES)
    assert not check_contract(distinct, calls("bob", "bob"), SIGNATURES)
    filtered = SEND.where(lambda e: e.msg == "private").distinct(lambda e: e.addr)
    assert check_contract(filtered, calls("bob", "bob"), SIGNATURES)


@pytest.mark.parametrize(
    ("op", "limit", "expected"),
    [("<", 3, True), ("<=", 2, True), (">", 2, False), (">=", 3, False)],
)
def test_count_comparisons(op: str, limit: int, expected: bool) -> None:
    spec = ComparisonSpec(SEND.count(), op, limit)
    assert check_contract(spec, calls("bob", "eve"), SIGNATURES) is expected


def test_sum_uses_actual_call_values_and_filter() -> None:
    spec = SEND.where(lambda e: e.addr == "bob").sum(lambda e: e.amount) <= 5
    trace: list[EffectCall] = [
        ("trusted.send", ("bob",), {"amount": 3}),
        ("trusted.send", ("eve",), {"amount": 100}),
        ("trusted.send", ("bob",), {"amount": 2}),
    ]
    assert check_contract(spec, trace, SIGNATURES)
    assert not check_contract(spec, [*trace, *calls("bob")], SIGNATURES)
    assert check_contract(SEND.count() <= 0, [], SIGNATURES)


def test_shared_value_requires_both_filtered_sides() -> None:
    left = SEND.where(lambda e: e.addr == "bob")
    right = SEND.where(lambda e: e.addr == "eve")
    spec = left.shares_value(right, lambda e: e.msg)
    assert check_contract(spec, calls("bob", "eve"), SIGNATURES)
    assert not check_contract(spec, calls("bob"), SIGNATURES)
    assert not check_contract(spec, [], SIGNATURES)
    trace: list[EffectCall] = [
        ("trusted.send", ("bob", "a"), {}),
        ("trusted.send", ("eve", "b"), {}),
    ]
    assert not check_contract(spec, trace, SIGNATURES)


def test_predicates_use_python_unicode_and_operand_returning_boolops() -> None:
    # Python counts Unicode code points, and `or` returns an operand rather
    # than a bool. The symbolic expression compiler is intentionally unused.
    spec = SEND.all(lambda e: len(e.msg) == 1 and (e.addr or "fallback") == "fallback")
    assert check_contract(spec, [("trusted.send", ("", "😀"), {})], SIGNATURES)
    assert not check_contract(spec, [("trusted.send", ("", "two"), {})], SIGNATURES)
    prefixes = SEND.all(lambda e: e.msg.startswith("雪") and not e.msg.endswith("!"))
    assert check_contract(prefixes, [("trusted.send", ("bob", "雪山"), {})], SIGNATURES)


def test_predicates_short_circuit_in_python() -> None:
    spec = SEND.all(lambda e: e.addr == "bob" or len(e.amount) > 0)
    assert check_contract(spec, calls("bob"), SIGNATURES)
    with pytest.raises(ContractEvaluationError, match="TypeError"):
        check_contract(spec, calls("eve"), SIGNATURES)


def test_predicates_cannot_call_captured_functions_or_access_runtime_attributes() -> (
    None
):
    def should_not_run(value: str) -> bool:
        raise AssertionError("a predicate must not execute an arbitrary callable")

    spec = SEND.all(lambda e: should_not_run(e.addr))
    with pytest.raises(
        ContractEvaluationError, match="unsupported contract expression"
    ):
        check_contract(spec, calls("bob"), SIGNATURES)
    object_access = AllSpec(
        SEND, LambdaSpec("e", ast.parse("e.__class__", mode="eval").body, {})
    )
    with pytest.raises(ContractEvaluationError, match="unknown concrete effect fields"):
        check_contract(object_access, calls("bob"), SIGNATURES)


def test_special_parameter_names_are_data_not_python_object_attributes() -> None:
    def special(__class__: str) -> None:
        raise AssertionError("no effect execution")

    spec = effect(special).all(lambda e: e.__class__ == "data")
    assert check_contract(
        spec,
        [("special", ("data",), {})],
        {"special": inspect.signature(special)},
    )


class UserRow(Row):
    email: str
    consented: bool


def test_column_comparison_requires_observed_sources() -> None:
    column = UserRow.email
    spec = SEND.all(lambda e: e.addr == column)
    with pytest.raises(ContractEvaluationError, match="row_sources"):
        check_contract(spec, [], SIGNATURES)
    assert not check_contract(spec, calls("bob"), SIGNATURES, row_sources=[])
    rows = [UserRow(email="bob", consented=True)]
    assert check_contract(spec, calls("bob"), SIGNATURES, row_sources=rows)
    assert not check_contract(spec, calls("eve"), SIGNATURES, row_sources=rows)
    reversed_spec = SEND.all(lambda e: column != e.addr)
    assert check_contract(reversed_spec, calls("eve"), SIGNATURES, row_sources=rows)


def test_column_schema_equivalence_survives_trusted_module_reload() -> None:
    column = UserRow.email
    spec = SEND.all(lambda e: e.addr == column)
    reloaded = type(
        "UserRow",
        (Row,),
        {
            "__module__": UserRow.__module__,
            "__qualname__": UserRow.__qualname__,
            "__annotations__": dict(UserRow.__annotations__),
        },
    )
    assert check_contract(
        spec,
        calls("bob"),
        SIGNATURES,
        row_sources=[reloaded(email="bob", consented=True)],
    )
    alien = type("OtherRow", (Row,), {"__annotations__": dict(UserRow.__annotations__)})
    assert not check_contract(
        spec, calls("bob"), SIGNATURES, row_sources=[alien(email="bob", consented=True)]
    )


def test_fluents_follow_trace_order_and_preserve_untouched_initial_values() -> None:
    locked = fluent("locked", value=bool, initial=True)

    @fluent_effect(lambda door, value: locked.set(door, value))
    def change(door: str, value: bool) -> None:
        raise AssertionError("no trusted code execution")

    signatures = {"home.change": inspect.signature(change)}
    unlocked: EffectCall = ("home.change", ("front", False), {})
    relocked: EffectCall = ("home.change", ("front", True), {})
    all_locked = locked.final.all(lambda e: e.value)
    assert check_contract(all_locked, [], signatures)
    assert check_contract(all_locked, [unlocked, relocked], signatures)
    assert not check_contract(all_locked, [relocked, unlocked], signatures)
    assert check_contract(locked.final["back"] == True, [unlocked], signatures)  # noqa: E712
    assert check_contract(locked.final["front"] != True, [unlocked], signatures)  # noqa: E712
    all_unlocked = locked.final.all(lambda e: not e.value)
    assert not check_contract(all_unlocked, [unlocked], signatures)


def test_fluent_all_refuses_infinite_key_quantification_but_handles_bool_keys() -> None:
    infinite = fluent("infinite", value=bool, initial=True)
    spec = infinite.final.all(lambda e: e.key == "front")
    with pytest.raises(ContractEvaluationError, match="infinite key domain"):
        check_contract(spec, [], {})
    finite = fluent("finite", key=bool, value=bool, initial=True)
    finite_spec = finite.final.all(lambda e: e.key or e.value)
    assert check_contract(finite_spec, [], {})


def test_unknown_specs_names_and_predicates_never_pass_vacuously() -> None:
    with pytest.raises(ContractEvaluationError, match="unsupported contract spec"):
        check_contract(ContractSpec(), [], SIGNATURES)
    with pytest.raises(ContractEvaluationError, match="unknown trusted effect"):
        check_contract(effect("misspelled").empty(), [], SIGNATURES)
    unknown_field = AllSpec(
        SEND, LambdaSpec("e", ast.parse("e.misspelled", mode="eval").body, {})
    )
    with pytest.raises(ContractEvaluationError, match="unknown concrete effect fields"):
        check_contract(unknown_field, [], SIGNATURES)
    unsupported = AllSpec(
        SEND, LambdaSpec("e", ast.parse("e.addr.upper()", mode="eval").body, {})
    )
    with pytest.raises(
        ContractEvaluationError, match="unsupported contract expression"
    ):
        check_contract(unsupported, [], SIGNATURES)
    with pytest.raises(ContractEvaluationError, match="missing trusted signature"):
        check_contract(no_guarantees(), [("evil.send", (), {})], {})
    with pytest.raises(ContractEvaluationError, match="TypeError"):
        check_contract(no_guarantees(), [("trusted.send", (), {})], SIGNATURES)


def test_literal_solver_values_are_decoded_without_symbolic_contract_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("symbolic predicate compiler must not be used")

    monkeypatch.setattr(LambdaSpec, "evaluate", forbidden)
    monkeypatch.setattr(AllSpec, "solve", forbidden)
    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    allow: Any = types.list.val(
        [types.str.val("bob", ctx=ctx), types.str.val("雪", ctx=ctx)], ctx=ctx
    )
    spec = SEND.all(lambda e: e.addr in allow)
    assert check_contract(spec, calls("bob", "雪"), SIGNATURES)
    assert not check_contract(spec, calls("eve"), SIGNATURES)
    assert check_contract(
        SEND.count() <= types.int.val(2, ctx=ctx), calls("bob", "eve"), SIGNATURES
    )
    with pytest.raises(ContractEvaluationError, match="nonliteral"):
        check_contract(SEND.count() <= types.int(expr=z3.Int("limit")), [], SIGNATURES)


@pytest.mark.parametrize(
    "text", ["", "\U00030000", "\U0010ffff", "\ud800", r"\u{30000}"]
)
def test_full_unicode_proxy_literals_keep_independent_contract_semantics(
    text: str,
) -> None:
    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    allowed = types.str.val(text, ctx=ctx)
    spec = SEND.all(lambda e: e.addr == allowed)
    assert check_contract(spec, calls(text), SIGNATURES)
    assert not check_contract(spec, calls(text + "x"), SIGNATURES)
    choices: Any = types.list.val([allowed], ctx=ctx)
    membership = SEND.all(lambda e: e.addr in choices)
    assert check_contract(membership, calls(text), SIGNATURES)
    assert not check_contract(membership, calls(text + "x"), SIGNATURES)


def test_symbolic_string_contract_literal_is_refused_without_model_completion() -> None:
    allowed = types.str(expr=z3.Const("unknown_address", string_sort()))
    spec = SEND.all(lambda e: e.addr == allowed)
    with pytest.raises(ContractEvaluationError, match="nonliteral"):
        check_contract(spec, calls(""), SIGNATURES)

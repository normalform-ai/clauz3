"""String literals must denote distinct fluent keys and exact initial values."""

import pytest
import z3

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._context._layer import FactInfo
from clauz3._vendor.deal_solver._proxies import types
from clauz3.fluent import _ParamRef, fluent
from clauz3.spec import ContractSpec


def _proved(spec: ContractSpec, ctx: Context) -> bool:
    solver = z3.Solver()
    solver.add(z3.Not(spec.solve(ctx=ctx).expr))
    return bool(solver.check() == z3.unsat)


def test_literal_backslash_key_is_distinct_from_decoded_character() -> None:
    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    state = fluent("string_key", value=bool, initial=True)
    state._register_effect(
        func_name="disable", update=state.set(_ParamRef("key"), False)
    )
    ctx.facts.add(
        FactInfo(
            name="disable",
            markers=("trusted",),
            args={"key": types.str.val("A", ctx=ctx)},
            cond=types.bool.val(True, ctx=ctx),
        )
    )

    assert _proved(state.final[r"\u{41}"] == True, ctx)  # noqa: E712
    assert not _proved(state.final[r"\u{41}"] == False, ctx)  # noqa: E712
    assert _proved(state.final["A"] == False, ctx)  # noqa: E712


def test_fluent_initial_string_does_not_interpret_backslash_escapes() -> None:
    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    state = fluent("string_value", value=str, initial=r"\u{41}")

    assert _proved(state.final["key"] == r"\u{41}", ctx)
    assert not _proved(state.final["key"] == "A", ctx)


def test_quantified_fluent_keys_span_exactly_python_strings() -> None:
    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    state = fluent("full_unicode_keys", value=bool, initial=True)

    assert _proved(
        state.final.all(lambda d: len(d.key) != 1 or d.key <= "\U0010ffff"), ctx
    )
    assert not _proved(
        state.final.all(lambda d: len(d.key) != 1 or d.key <= "\U0002ffff"), ctx
    )


@pytest.mark.parametrize("location", ["initial", "key"])
def test_fluent_literal_above_native_z3_character_range_is_exact(location: str) -> None:
    ctx = Context.make_empty(get_contracts=lambda _: iter(()))
    state = fluent(
        "full_unicode_string",
        value=str,
        initial="\U00030000" if location == "initial" else "valid",
    )
    key = "\U00030000" if location == "key" else "valid"

    expected = "\U00030000" if location == "initial" else "valid"
    assert _proved(state.final[key] == expected, ctx)
    assert not _proved(state.final[key] == expected + "other", ctx)

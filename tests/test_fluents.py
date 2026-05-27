"""Stateful (fluent) contracts: successor-state folding over the fact trace.

These tests build the recorded fact trace directly and discharge fluent
contracts against it, so they pin the load-bearing properties of the encoding
without the full prove pipeline: program order matters (last write wins),
branch guards thread through correctly, and effects inside loops fail closed.
"""

from __future__ import annotations

import pytest
import z3

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._context._layer import FactInfo
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import BoolSort, types
from clauz3.fluent import Fluent, _ParamRef, effect, fluent
from clauz3.spec import ContractSpec


def _ctx() -> Context:
    return Context.make_empty(get_contracts=lambda _: iter(()))


def _door_locked() -> Fluent:
    """A fresh door-locked fluent with unlock/lock successor-state effects."""
    door_locked = fluent("door_locked", key=str, value=bool, initial=True)
    door_locked._register_effect(
        func_name="unlock_door",
        update=door_locked.set(_ParamRef("door"), False),
    )
    door_locked._register_effect(
        func_name="lock_door",
        update=door_locked.set(_ParamRef("door"), True),
    )
    return door_locked


def _fact(name: str, *, door: str, cond: BoolSort, ctx: Context) -> FactInfo:
    return FactInfo(
        name=name,
        markers=("trusted",),
        args={"door": types.str.val(door, ctx=ctx)},
        cond=cond,
    )


def _is_proved(spec: ContractSpec, ctx: Context) -> bool:
    """True iff the guarantee holds for every model (Not(spec) is unsat)."""
    result = spec.solve(ctx=ctx)
    solver = z3.Solver()
    solver.add(z3.Not(result.expr))
    return bool(solver.check() == z3.unsat)


def _is_violated(spec: ContractSpec, ctx: Context) -> bool:
    result = spec.solve(ctx=ctx)
    solver = z3.Solver()
    solver.add(z3.Not(result.expr))
    return bool(solver.check() == z3.sat)


def test_unlock_then_lock_leaves_all_doors_locked() -> None:
    ctx = _ctx()
    door_locked = _door_locked()
    true = types.bool.val(True, ctx=ctx)
    ctx.facts.add(_fact("unlock_door", door="front", cond=true, ctx=ctx))
    ctx.facts.add(_fact("lock_door", door="front", cond=true, ctx=ctx))

    spec = door_locked.final.all(lambda d: d.value)
    assert _is_proved(spec, ctx)


def test_unlock_without_lock_violates_all_locked() -> None:
    ctx = _ctx()
    door_locked = _door_locked()
    true = types.bool.val(True, ctx=ctx)
    ctx.facts.add(_fact("unlock_door", door="front", cond=true, ctx=ctx))

    spec = door_locked.final.all(lambda d: d.value)
    assert _is_violated(spec, ctx)


def test_order_matters_lock_then_unlock_is_unlocked() -> None:
    # Same fact *set* as the passing case, opposite order: the multiset relation
    # language cannot tell these apart, but the fluent fold must.
    ctx = _ctx()
    door_locked = _door_locked()
    true = types.bool.val(True, ctx=ctx)
    ctx.facts.add(_fact("lock_door", door="front", cond=true, ctx=ctx))
    ctx.facts.add(_fact("unlock_door", door="front", cond=true, ctx=ctx))

    spec = door_locked.final.all(lambda d: d.value)
    assert _is_violated(spec, ctx)


def test_per_door_final_value() -> None:
    ctx = _ctx()
    door_locked = _door_locked()
    true = types.bool.val(True, ctx=ctx)
    # back unlocked then relocked; front unlocked and left open.
    ctx.facts.add(_fact("unlock_door", door="back", cond=true, ctx=ctx))
    ctx.facts.add(_fact("lock_door", door="back", cond=true, ctx=ctx))
    ctx.facts.add(_fact("unlock_door", door="front", cond=true, ctx=ctx))

    assert _is_proved(door_locked.final["back"] == True, ctx)  # noqa: E712
    assert _is_violated(door_locked.final["front"] == True, ctx)  # noqa: E712


def test_branch_guard_threads_through_final_state() -> None:
    # Unlock only happens on one branch; an unconditional re-lock follows. The
    # final state is locked regardless of which branch was taken.
    ctx = _ctx()
    door_locked = _door_locked()
    branch = types.bool(expr=z3.Bool("branch"))
    true = types.bool.val(True, ctx=ctx)
    ctx.facts.add(_fact("unlock_door", door="front", cond=branch, ctx=ctx))
    ctx.facts.add(_fact("lock_door", door="front", cond=true, ctx=ctx))

    spec = door_locked.final.all(lambda d: d.value)
    assert _is_proved(spec, ctx)


def test_conditional_unlock_without_relock_is_unprovable() -> None:
    # Unlock guarded by an unconstrained branch and never re-locked: there is a
    # model (branch taken) leaving the door open, so the guarantee must not hold.
    ctx = _ctx()
    door_locked = _door_locked()
    branch = types.bool(expr=z3.Bool("branch"))
    ctx.facts.add(_fact("unlock_door", door="front", cond=branch, ctx=ctx))

    spec = door_locked.final.all(lambda d: d.value)
    assert _is_violated(spec, ctx)


def test_effect_inside_loop_fails_closed() -> None:
    ctx = _ctx()
    door_locked = _door_locked()
    true = types.bool.val(True, ctx=ctx)
    fact = FactInfo(
        name="unlock_door",
        markers=("trusted",),
        args={"door": types.str.val("front", ctx=ctx)},
        cond=true,
        quantifiers=(object(),),
    )
    ctx.facts.add(fact)

    with pytest.raises(UnsupportedError):
        (door_locked.final["front"] == True).solve(ctx=ctx)  # noqa: E712


def test_effect_decorator_records_param_and_literal() -> None:
    setpoint = fluent("setpoint", key=str, value=int, initial=70)

    @effect(lambda zone, temp: setpoint.set(zone, temp))
    def set_point(zone: str, temp: int) -> None: ...

    updates = setpoint._effects["set_point"]
    assert len(updates) == 1
    assert updates[0].key == _ParamRef("zone")
    assert updates[0].value == _ParamRef("temp")


def test_int_valued_fluent_final_compare() -> None:
    setpoint = fluent("setpoint", key=str, value=int, initial=70)
    setpoint._register_effect(
        func_name="set_point",
        update=setpoint.set(_ParamRef("zone"), _ParamRef("temp")),
    )
    ctx = _ctx()
    true = types.bool.val(True, ctx=ctx)
    ctx.facts.add(
        FactInfo(
            name="set_point",
            markers=("trusted",),
            args={
                "zone": types.str.val("bedroom", ctx=ctx),
                "temp": types.int.val(65, ctx=ctx),
            },
            cond=true,
        )
    )

    assert _is_proved(setpoint.final["bedroom"] == 65, ctx)
    assert _is_violated(setpoint.final["bedroom"] == 70, ctx)


def test_unsupported_value_type_rejected() -> None:
    with pytest.raises(UnsupportedError):
        fluent("bad", key=str, value=float, initial=1.0)

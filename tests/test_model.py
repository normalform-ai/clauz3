"""Counterexample model rendering."""

from __future__ import annotations

import z3

from clauz3._vendor.deal_solver._model import Model


def test_model_renders_z3_datatype_value_without_raising() -> None:
    """Regression for #14: Model.__iter__ used to call eval(repr(z3_val))
    against a fixed GLOBALS dict that didn't include user-defined datatype
    constructors (e.g. `mk_userrow(...)`), so any failing proof whose model
    contained a row-typed value raised NameError mid-render.

    The fix falls back to ``str(z3_val)`` when the eval can't resolve a
    constructor name. This test exercises that fallback directly.
    """
    user_row = z3.Datatype("UserRow")
    user_row.declare(
        "mk_userrow",
        ("name", z3.StringSort()),
        ("email", z3.StringSort()),
        ("consented", z3.BoolSort()),
    )
    user_row = user_row.create()

    s = z3.Solver()
    row = z3.Const("row", user_row)
    s.add(
        row
        == user_row.mk_userrow(
            z3.StringVal("Bob"),
            z3.StringVal("bob@example.com"),
            z3.BoolVal(True),
        )
    )
    assert s.check() == z3.sat

    model = Model(s.model())
    rendered = dict(model)
    # The fallback emits a Python string carrying the Z3 repr.
    assert "row" in rendered
    row_value = rendered["row"]
    assert isinstance(row_value, str)
    assert "mk_userrow" in row_value
    assert "bob@example.com" in row_value


def test_model_str_does_not_raise_on_datatype_value() -> None:
    """Same scenario, exercised through ``str(model)`` which is the path
    `clauz3 prove`'s CLI hits when it prints a failed-guarantee example."""
    user_row = z3.Datatype("UserRow")
    user_row.declare("mk_userrow", ("email", z3.StringSort()))
    user_row = user_row.create()

    s = z3.Solver()
    row = z3.Const("row", user_row)
    s.add(row == user_row.mk_userrow(z3.StringVal("ann@example.com")))
    assert s.check() == z3.sat

    rendered = str(Model(s.model()))
    assert "ann@example.com" in rendered


def test_model_still_renders_primitive_sorts_via_eval() -> None:
    """The eval path is preserved for primitive Z3 sorts that GLOBALS does
    know about (Int, Real, etc.) — only the unrenderable datatype case
    falls back to ``str``."""
    s = z3.Solver()
    n = z3.Int("n")
    s.add(n == 42)
    assert s.check() == z3.sat
    rendered = dict(Model(s.model()))
    assert rendered == {"n": 42}

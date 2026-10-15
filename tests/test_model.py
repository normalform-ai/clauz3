"""Counterexample model rendering."""

from __future__ import annotations

import pytest
import z3

from clauz3._vendor.deal_solver._model import Model
from clauz3._vendor.deal_solver._strings import string_sort, string_val


def test_model_renders_z3_datatype_value_without_raising() -> None:
    """Rows remain display strings, with decoded string fields (#14, #209)."""
    user_row = z3.Datatype("UserRow")
    user_row.declare(
        "mk_userrow",
        ("name", string_sort()),
        ("email", string_sort()),
        ("consented", z3.BoolSort()),
    )
    user_row = user_row.create()

    s = z3.Solver()
    row = z3.Const("row", user_row)
    s.add(
        row
        == user_row.mk_userrow(
            string_val("Bob"),
            string_val("bob@example.com"),
            z3.BoolVal(True),
        )
    )
    assert s.check() == z3.sat

    model = Model(s.model())
    rendered = dict(model)
    # Preserve the display-string representation instead of constructing a Row.
    assert "row" in rendered
    row_value = rendered["row"]
    assert isinstance(row_value, str)
    assert "mk_userrow" in row_value
    assert "bob@example.com" in row_value


def test_model_str_does_not_raise_on_datatype_value() -> None:
    """Same scenario, exercised through ``str(model)`` which is the path
    `clauz3 prove`'s CLI hits when it prints a failed-guarantee example."""
    user_row = z3.Datatype("UserRow")
    user_row.declare("mk_userrow", ("email", string_sort()))
    user_row = user_row.create()

    s = z3.Solver()
    row = z3.Const("row", user_row)
    s.add(row == user_row.mk_userrow(string_val("ann@example.com")))
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


def _render(value: z3.ExprRef) -> object:
    variable = z3.Const("value", value.sort())
    solver = z3.Solver(ctx=value.ctx)
    solver.add(variable == value)
    assert solver.check() == z3.sat
    return dict(Model(solver.model()))["value"]


@pytest.mark.parametrize("custom_context", [False, True])
def test_model_decodes_strings_in_lists_and_nested_lists(custom_context: bool) -> None:
    ctx = z3.Context() if custom_context else None
    texts = ["AB", "", "\ud800\U00030000\U0010ffff"]
    values = z3.Concat(*(z3.Unit(string_val(text, ctx)) for text in texts))
    assert _render(values) == texts
    assert _render(z3.Unit(values)) == [texts]
    assert _render(z3.Empty(z3.SeqSort(string_sort(ctx)))) == []


@pytest.mark.parametrize("custom_context", [False, True])
def test_model_decodes_string_dictionary_keys_and_values(custom_context: bool) -> None:
    ctx = z3.Context() if custom_context else None
    entry = z3.Datatype("dict_val__str", ctx=ctx)
    entry.declare("new", ("exists", z3.BoolSort(ctx)), ("value", string_sort(ctx)))
    entry_sort = entry.create()
    items = z3.K(
        string_sort(ctx), entry_sort.new(z3.BoolVal(False, ctx), string_val("", ctx))
    )
    key, value = "key\U00030000", "value\U0010ffff"
    items = z3.Store(
        items,
        string_val(key, ctx),
        entry_sort.new(z3.BoolVal(True, ctx), string_val(value, ctx)),
    )
    assert _render(items) == {key: value}


@pytest.mark.parametrize("custom_context", [False, True])
def test_model_decodes_string_set_members(custom_context: bool) -> None:
    ctx = z3.Context() if custom_context else None
    items = z3.EmptySet(string_sort(ctx))
    for text in ["AB", "\U0010ffff"]:
        items = z3.SetAdd(items, string_val(text, ctx))
    assert _render(items) == {"AB", "\U0010ffff"}


def test_model_decodes_row_fields_inside_query_array() -> None:
    row = z3.Datatype("QueryRow")
    row.declare("mk_queryrow", ("text", string_sort()))
    row_sort = row.create()
    text = "\ud800\U00030000\U0010ffff"
    rows = z3.Store(
        z3.K(z3.IntSort(), row_sort.mk_queryrow(string_val(""))),
        0,
        row_sort.mk_queryrow(string_val(text)),
    )
    assert _render(rows) == {0: f"mk_queryrow({text!r})"}


def test_model_preserves_nonstring_primitives_and_collections() -> None:
    assert _render(z3.RealVal("3/2")) == 1.5
    assert _render(z3.FPVal(-2.5, z3.Float64())) == -2.5
    assert _render(z3.BoolVal(True)) is True
    assert _render(z3.Concat(z3.Unit(z3.IntVal(65)), z3.Unit(z3.IntVal(66)))) == [
        65,
        66,
    ]
    assert _render(z3.Store(z3.K(z3.IntSort(), z3.IntVal(0)), 1, 2)) == {1: 2}
    assert _render(z3.SetAdd(z3.EmptySet(z3.IntSort()), z3.IntVal(1))) == {1}


def test_invalid_internal_string_in_a_container_remains_printable() -> None:
    invalid = z3.Unit(z3.BitVecVal(0x110000, 21))
    assert _render(z3.Unit(invalid)) == [str(invalid)]

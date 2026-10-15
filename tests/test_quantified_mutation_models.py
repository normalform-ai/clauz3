"""Collection writes must not escape a discarded quantified-loop context."""

from textwrap import dedent

import astroid
import pytest
import z3

from clauz3._vendor.deal_solver._context import Context, _trace
from clauz3._vendor.deal_solver._eval_stmt import eval_stmt
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import (
    DictSort,
    ListSort,
    ProxySort,
    SetSort,
    UntypedDictSort,
    UntypedListSort,
    UntypedSetSort,
)

# Use the evaluator's trace protocol spelling so these semantic regressions
# also run against the pre-guard source, where the exported constant is absent.
_QUANTIFIED_LOOP_BODY = "<quantified loop body>"
_METHOD_REFUSAL = "mutating methods inside a quantified loop are not supported"
_SUBSCRIPT_REFUSAL = "subscript assignment inside a quantified loop is not supported"
_BINDING_REFUSAL = "mutating method requires a direct call on a named receiver"


def _context() -> Context:
    return Context.make_empty(get_contracts=lambda _: iter(()))


def _evaluate(source: str, ctx: Context) -> None:
    for statement in astroid.parse(dedent(source)).body:
        eval_stmt(statement, ctx=ctx)


def _all_assertions_hold(ctx: Context) -> bool:
    assert ctx.expected.layer, "the control must evaluate an assertion"
    return all(z3.is_true(z3.simplify(item.expr)) for item in ctx.expected)


def test_guard_protocol_matches_the_exported_marker() -> None:
    assert _QUANTIFIED_LOOP_BODY == _trace.QUANTIFIED_LOOP_BODY


@pytest.mark.parametrize(
    ("model_types", "mutators"),
    [
        ((ListSort, UntypedListSort), ("append", "extend", "clear")),
        (
            (SetSort, UntypedSetSort),
            (
                "add",
                "clear",
                "discard",
                "remove",
                "pop",
                "update",
                "intersection_update",
                "difference_update",
                "symmetric_difference_update",
            ),
        ),
        ((DictSort, UntypedDictSort), ("__setitem__", "clear", "pop")),
    ],
    ids=["list", "set", "dict"],
)
def test_modelled_mutators_keep_the_metadata_that_enables_the_guard(
    model_types: tuple[type[ProxySort], ...], mutators: tuple[str, ...]
) -> None:
    for model in model_types:
        for name in mutators:
            method = model.methods.get(name)
            assert method is not None, (model.__name__, name)
            assert not method.pure, (model.__name__, name)


@pytest.mark.parametrize("model", [DictSort, UntypedDictSort])
def test_dictionary_getitem_is_registered_as_a_read(model: type[ProxySort]) -> None:
    method = model.methods.get("__getitem__")
    assert method is not None
    assert method.pure


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        pytest.param(
            "values = [0]\nvalues.append(1)",
            _METHOD_REFUSAL,
            id="list-append",
        ),
        pytest.param(
            "values = {'x': 0}\nvalues.clear()",
            _METHOD_REFUSAL,
            id="dictionary-clear",
        ),
        pytest.param(
            "values = {'x': 0}\nvalues['x'] = 1",
            _SUBSCRIPT_REFUSAL,
            id="dictionary-subscript",
        ),
        pytest.param(
            "values = [0]\nvalues[0] = 1",
            _SUBSCRIPT_REFUSAL,
            id="list-subscript-refused-before-model-dispatch",
        ),
        pytest.param(
            "values = [0]\nappend = values.append\nappend(1)",
            _BINDING_REFUSAL,
            id="bound-method-alias",
        ),
        pytest.param(
            "values = [0]\nvalues.append.__call__(1)",
            _BINDING_REFUSAL,
            id="wrapped-method-model",
        ),
        pytest.param(
            "values = [0]\nif True:\n    values.append(1)",
            _METHOD_REFUSAL,
            id="branch-child-context",
        ),
        pytest.param(
            """
            def append_one(items):
                if True:
                    items.append(1)
                return 0
            values = [0]
            append_one(values)
            """,
            "mutating a function parameter or its alias is not supported",
            id="detached-helper-and-branch",
        ),
        pytest.param(
            """
            def invoke(callback):
                callback(1)
                return 0
            values = [0]
            append = values.append
            invoke(append)
            """,
            _BINDING_REFUSAL,
            id="bound-method-passed-to-helper",
        ),
        pytest.param(
            """
            def build():
                values = []
                values.append(1)
                return values
            build()
            """,
            _METHOD_REFUSAL,
            id="helper-local-container",
        ),
        pytest.param(
            """
            def put(mapping):
                mapping['x'] = 1
                return 0
            values = {'x': 0}
            put(values)
            """,
            _SUBSCRIPT_REFUSAL,
            id="helper-subscript-bypasses-method-dispatch",
        ),
        pytest.param(
            "values = [0]\nfor item in [1]:\n    values.append(item)",
            _METHOD_REFUSAL,
            id="nested-literal-loop-inherits-context",
        ),
    ],
)
def test_mutation_is_refused_in_quantified_body_context(
    source: str, diagnostic: str
) -> None:
    ctx = _context()

    with (
        ctx.trace.guard(_QUANTIFIED_LOOP_BODY),
        pytest.raises(UnsupportedError, match=diagnostic),
    ):
        _evaluate(source, ctx)


def test_a_shared_child_context_retains_the_quantified_body_marker() -> None:
    ctx = _context()
    _evaluate("values = [0]", ctx)
    with ctx.trace.guard(_QUANTIFIED_LOOP_BODY):
        child = ctx.make_child()
        assert child.trace is ctx.trace
        with pytest.raises(UnsupportedError, match=_METHOD_REFUSAL):
            _evaluate("values.append(1)", child)


@pytest.mark.parametrize("expected", [True, False], ids=["correct", "wrong"])
def test_empty_list_concatenation_remains_pure_inside_the_guard(
    expected: bool,
) -> None:
    ctx = _context()
    expected_result = "[1]" if expected else "[]"

    with ctx.trace.guard(_QUANTIFIED_LOOP_BODY):
        _evaluate(
            "values = []\n"
            "result = values + [1]\n"
            "assert values == []\n"
            f"assert result == {expected_result}\n",
            ctx,
        )

    assert _all_assertions_hold(ctx) is expected


def test_dictionary_construction_is_not_subscript_assignment() -> None:
    ctx = _context()

    with ctx.trace.guard(_QUANTIFIED_LOOP_BODY):
        _evaluate("values = {'x': 1}\nassert values == {'x': 1}", ctx)

    assert _all_assertions_hold(ctx)


@pytest.mark.parametrize("expected", [True, False], ids=["correct", "wrong"])
def test_dictionary_subscript_read_preserves_the_receiver_inside_guard(
    expected: bool,
) -> None:
    # Source dictionary reads use subscripts; explicit __getitem__ attributes
    # are refused by the interpreter-surface gate.
    ctx = _context()
    selected = 1 if expected else 2

    with ctx.trace.guard(_QUANTIFIED_LOOP_BODY):
        _evaluate(
            "values = {'x': 1}\n"
            "result = values['x']\n"
            "assert values == {'x': 1}\n"
            f"assert result == {selected}\n",
            ctx,
        )

    assert _all_assertions_hold(ctx) is expected


@pytest.mark.parametrize("expected", [True, False], ids=["correct", "wrong"])
@pytest.mark.parametrize(
    ("body", "assertion", "wrong_assertion"),
    [
        (
            "values = [0]\nfor item in [1, 2]:\n    values.append(item)\n",
            "values == [0, 1, 2]",
            "values == [0]",
        ),
        (
            "values = {0: 0}\nfor item in [1, 2]:\n    values[item] = item\n",
            "values[2] == 2",
            "values[2] == 0",
        ),
    ],
    ids=["literal-loop-append", "literal-loop-subscript"],
)
def test_literal_loops_outside_the_guard_preserve_collection_updates(
    expected: bool, body: str, assertion: str, wrong_assertion: str
) -> None:
    ctx = _context()
    selected = assertion if expected else wrong_assertion

    _evaluate(body + f"assert {selected}\n", ctx)

    assert _all_assertions_hold(ctx) is expected


def test_nested_refusal_keeps_outer_marker_then_releases_it() -> None:
    ctx = _context()
    _evaluate("values = [0]", ctx)

    with ctx.trace.guard(_QUANTIFIED_LOOP_BODY):
        with (
            pytest.raises(UnsupportedError, match=_METHOD_REFUSAL),
            ctx.trace.guard(_QUANTIFIED_LOOP_BODY),
        ):
            _evaluate("values.append(1)", ctx)
        assert _QUANTIFIED_LOOP_BODY in ctx.trace
        with pytest.raises(UnsupportedError, match=_METHOD_REFUSAL):
            _evaluate("values.append(1)", ctx)

    assert _QUANTIFIED_LOOP_BODY not in ctx.trace
    _evaluate("values.append(1)\nassert values == [0, 1]", ctx)
    assert _all_assertions_hold(ctx)

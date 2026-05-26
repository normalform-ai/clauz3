"""Relation-lambda string method support (startswith / endswith)."""

from __future__ import annotations

import pytest
import z3

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import types
from clauz3.spec import LambdaSpec


def _ctx() -> Context:
    return Context.make_empty(get_contracts=lambda _: iter(()))


def test_startswith_true_and_false() -> None:
    ctx = _ctx()
    spec = LambdaSpec.from_callable(lambda e: e.path.startswith("/sandbox"))

    inside = spec.evaluate(row={"path": types.str.val("/sandbox/a", ctx=ctx)}, ctx=ctx)
    outside = spec.evaluate(row={"path": types.str.val("/etc/x", ctx=ctx)}, ctx=ctx)

    assert z3.simplify(inside.expr) == z3.BoolVal(True)
    assert z3.simplify(outside.expr) == z3.BoolVal(False)


def test_endswith_true() -> None:
    ctx = _ctx()
    spec = LambdaSpec.from_callable(lambda e: e.path.endswith(".py"))

    result = spec.evaluate(row={"path": types.str.val("/repo/a.py", ctx=ctx)}, ctx=ctx)

    assert z3.simplify(result.expr) == z3.BoolVal(True)


def test_unsupported_method_fails_closed() -> None:
    ctx = _ctx()
    spec = LambdaSpec.from_callable(lambda e: e.path.upper())

    with pytest.raises(UnsupportedError):
        spec.evaluate(row={"path": types.str.val("/x", ctx=ctx)}, ctx=ctx)


def test_startswith_requires_single_argument() -> None:
    ctx = _ctx()
    spec = LambdaSpec.from_callable(lambda e: e.path.startswith("/a", 1))

    with pytest.raises(UnsupportedError):
        spec.evaluate(row={"path": types.str.val("/a", ctx=ctx)}, ctx=ctx)


def test_multiline_lambda_body_is_not_truncated() -> None:
    ctx = _ctx()
    # A lambda passed as a call argument across continuation lines: a regression
    # guard against from_callable capturing only the lambda's first physical
    # line, which would silently drop the trailing ``and`` clauses and let an
    # unsound contract prove. The final clause (``]``) must still be enforced.
    spec = LambdaSpec.from_callable(
        lambda e: (
            "(" not in e.text
            and ")" not in e.text
            and "[" not in e.text
            and "]" not in e.text
        )
    )

    only_last = spec.evaluate(row={"text": types.str.val("safe]now", ctx=ctx)}, ctx=ctx)
    clean = spec.evaluate(row={"text": types.str.val("safe now", ctx=ctx)}, ctx=ctx)

    assert z3.simplify(only_last.expr) == z3.BoolVal(False)
    assert z3.simplify(clean.expr) == z3.BoolVal(True)

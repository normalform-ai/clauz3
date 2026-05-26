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

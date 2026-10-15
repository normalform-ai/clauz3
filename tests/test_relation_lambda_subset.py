"""Conformance suite for the relation-lambda subset compiled by ``clauz3.spec``.

The relation lambda inside a ``@contract`` helper is never called. It is parsed
from source and compiled by ``LambdaSpec``; anything outside the subset must
raise ``UnsupportedError`` so the proof fails closed. This file pins the exact
boundary that ``docs/reference/python-subset.md`` documents, one construct per
case, so a change to the compiler that widens or narrows the subset shows up
as a test diff rather than a silent docs drift.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

import pytest
import z3

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import types
from clauz3.spec import LambdaSpec

ALLOWED = ["bob@example.com"]
LIMIT = 5
CAPTURED_DICT = {"a": 1}
CAPTURED_FLOAT = 1.5
CAPTURED_TUPLE = ("a", "b")
CAPTURED_SET = {"a"}
CAPTURED_NONE = None


def _row(ctx: Context) -> dict[str, Any]:
    return {
        "addr": types.str.val("bob@example.com", ctx=ctx),
        "n": types.int.val(3, ctx=ctx),
        "flag": types.bool.val(True, ctx=ctx),
    }


def _evaluate(predicate: Callable[[Any], Any], ctx: Context) -> Any:
    return LambdaSpec.from_callable(predicate).evaluate(row=_row(ctx), ctx=ctx)


@pytest.mark.parametrize(
    ("label", "predicate", "expected"),
    [
        (
            "str/int/bool literals",
            lambda e: e.addr == "x" and e.n < 1 and e.flag,
            False,
        ),
        ("list literal", lambda e: e.addr in ["a", "b"], False),
        ("empty list literal", lambda e: e.addr in [], False),
        ("captured list", lambda e: e.addr in ALLOWED, True),
        ("captured int", lambda e: e.n <= LIMIT, True),
        ("equal", lambda e: e.n == 3, True),
        ("not equal", lambda e: e.n != 3, False),
        ("less than", lambda e: e.n < 3, False),
        ("less or equal", lambda e: e.n <= 3, True),
        ("greater than", lambda e: e.n > 3, False),
        ("greater or equal", lambda e: e.n >= 3, True),
        ("in", lambda e: e.addr in ALLOWED, True),
        ("not in", lambda e: e.addr not in ALLOWED, False),
        ("substring in", lambda e: "@" in e.addr, True),
        ("and / or / not", lambda e: not (e.flag and e.n > 0 or e.flag), False),
        ("binary + and -", lambda e: e.n + 1 - 1 > 0, True),
        ("string concatenation", lambda e: e.addr + "x" == "y", False),
        ("len", lambda e: len(e.addr) <= 10, False),
        ("startswith", lambda e: e.addr.startswith("bob"), True),
        ("endswith", lambda e: e.addr.endswith(".com"), True),
        ("float literal", lambda e: e.n < 1.5, False),
        ("captured float", lambda e: e.n < CAPTURED_FLOAT, False),
        ("captured tuple", lambda e: e.addr in CAPTURED_TUPLE, False),
        ("captured set", lambda e: e.addr in CAPTURED_SET, False),
        ("captured dict", lambda e: e.addr in CAPTURED_DICT, False),
    ],
)
def test_accepted_construct(
    label: str,
    predicate: Callable[[Any], Any],
    expected: bool,
    ctx_factory: Callable[[], Context],
) -> None:
    result = _evaluate(predicate, ctx_factory())

    assert z3.is_true(z3.simplify(result.expr == z3.BoolVal(expected))), label


@pytest.mark.parametrize(
    ("label", "predicate", "message"),
    [
        ("None literal", lambda e: e.addr == None, "unsupported contract literal"),  # noqa: E711
        ("bytes literal", lambda e: e.addr == b"x", "unsupported contract literal"),
        ("tuple literal", lambda e: e.addr in ("a", "b"), "unsupported expression"),
        ("set literal", lambda e: e.addr in {"a", "b"}, "unsupported expression"),
        ("dict literal", lambda e: e.addr in {"a": 1}, "unsupported expression"),
        (
            "captured None",
            lambda e: e.addr == CAPTURED_NONE,
            "unsupported contract literal",
        ),
        ("bare row", lambda e: e, "bare effect row is not supported"),
        ("unknown field", lambda e: e.nope == 1, "unknown effect field"),
        ("nested attribute", lambda e: e.addr.real == 1, "only effect row attributes"),
        (
            "attribute of a capture",
            lambda e: e.n == LIMIT.real,
            "only effect row attributes",
        ),
        ("is", lambda e: e.addr is e.addr, "unsupported comparison"),
        ("is not", lambda e: e.addr is not e.addr, "unsupported comparison"),
        ("chained comparison", lambda e: 0 < e.n < 10, "chained comparisons"),
        ("unary minus", lambda e: e.n > -1, "unsupported expression"),
        ("unary plus", lambda e: e.n > +1, "unsupported expression"),
        ("multiplication", lambda e: e.n * 2 > 0, "unsupported binary operation"),
        ("division", lambda e: e.n / 2 > 0, "unsupported binary operation"),
        ("floor division", lambda e: e.n // 2 > 0, "unsupported binary operation"),
        ("modulo", lambda e: e.n % 2 > 0, "unsupported binary operation"),
        ("power", lambda e: e.n**2 > 0, "unsupported binary operation"),
        ("bitwise and", lambda e: (e.n & 1) == 1, "unsupported binary operation"),
        ("bitwise or", lambda e: (e.n | 1) == 1, "unsupported binary operation"),
        ("bitwise xor", lambda e: (e.n ^ 1) == 1, "unsupported binary operation"),
        ("left shift", lambda e: (e.n << 1) == 1, "unsupported binary operation"),
        ("right shift", lambda e: (e.n >> 1) == 1, "unsupported binary operation"),
        ("matrix multiply", lambda e: (e.n @ e.n) == 1, "unsupported binary operation"),
        ("invert", lambda e: ~e.n == 1, "unsupported expression"),
        ("method on int", lambda e: e.n.startswith("x"), "unsupported method"),
        (
            "startswith with two args",
            lambda e: e.addr.startswith("b", 1),
            "one positional",
        ),
        ("other str method", lambda e: e.addr.upper() == "X", "unsupported call"),
        ("builtin abs", lambda e: abs(e.n) > 0, "unsupported call"),
        ("builtin int", lambda e: int(e.n) > 0, "unsupported call"),
        ("builtin str", lambda e: str(e.n) == "3", "unsupported call"),
        ("builtin max", lambda e: max(e.n, 1) > 0, "unsupported call"),
        ("builtin bool", lambda e: bool(e.flag), "unsupported call"),
        (
            "conditional expression",
            lambda e: (1 if e.flag else 2) > 0,
            "unsupported expression",
        ),
        ("subscript", lambda e: e.addr[0] == "b", "unsupported expression"),
        ("slice", lambda e: e.addr[:3] == "bob", "unsupported expression"),
        ("f-string", lambda e: f"{e.addr}" == "x", "unsupported expression"),
        (
            "list comprehension",
            lambda e: e.addr in [a for a in ALLOWED],
            "unsupported expression",
        ),
        (
            "generator in any",
            lambda e: any(a == e.addr for a in ALLOWED),
            "unsupported call",
        ),
        ("nested lambda", lambda e: (lambda x: x)(e.n) > 0, "unsupported call"),
        ("walrus", lambda e: (m := e.n) > 0, "unsupported expression"),  # noqa: F841
    ],
)
def test_rejected_construct(
    label: str,
    predicate: Callable[[Any], Any],
    message: str,
    ctx_factory: Callable[[], Context],
) -> None:
    with pytest.raises(UnsupportedError, match=message):
        _evaluate(predicate, ctx_factory())


def test_two_parameter_lambda_is_rejected() -> None:
    two_parameters: Any = lambda a, b: a  # noqa: E731
    with pytest.raises(UnsupportedError, match="exactly one argument"):
        LambdaSpec.from_callable(two_parameters)


def _def_predicate(e: Any) -> Any:
    return e.n > 0


def test_def_instead_of_lambda_is_rejected() -> None:
    with pytest.raises(UnsupportedError, match="could not find lambda source"):
        LambdaSpec.from_callable(_def_predicate)


def test_unknown_capture_is_rejected(ctx_factory: Callable[[], Context]) -> None:
    predicate = LambdaSpec(
        arg_name="e",
        body=ast.parse("e.n == missing", mode="eval").body,
        env={},
    )
    ctx = ctx_factory()
    with pytest.raises(UnsupportedError, match="unknown name in effect lambda"):
        predicate.evaluate(row=_row(ctx), ctx=ctx)

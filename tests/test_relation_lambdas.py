"""Relation-lambda source location and string method support."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import z3

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._context._layer import FactInfo
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import ProxySort, types
from clauz3.spec import EffectRelation, LambdaSpec


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


def test_two_lambdas_on_one_line_compile_distinct_bodies() -> None:
    ctx = _ctx()
    # Regression guard for gh-125: ``from_callable`` located a lambda by line
    # number alone, so two relation lambdas sharing a physical line -- the
    # natural way to write ``Email.where(...).all(...)`` -- both compiled to the
    # first lambda's body. The ``all`` predicate silently became the ``where``
    # filter, which every filtered row satisfies vacuously. Both lambdas must
    # stay on one physical line for this to remain a regression guard.
    make = LambdaSpec.from_callable
    addr, msg = make(lambda e: e.addr == "bob"), make(lambda e: e.msg == "x")

    row: dict[str, ProxySort] = {
        "addr": types.str.val("bob", ctx=ctx),
        "msg": types.str.val("y", ctx=ctx),
    }

    assert z3.simplify(addr.evaluate(row=row, ctx=ctx).expr) == z3.BoolVal(True)
    assert z3.simplify(msg.evaluate(row=row, ctx=ctx).expr) == z3.BoolVal(False)


def test_one_line_where_all_keeps_both_predicates() -> None:
    # The end-to-end shape from gh-125, on a single source line. Bob is emailed
    # "y", so the guarantee must be violated; before the fix the ``all``
    # predicate was compiled as the ``where`` filter and the contract proved.
    ctx = _ctx()
    relation = EffectRelation(("send_email",))
    spec = relation.where(lambda e: e.addr == "bob").all(lambda e: e.msg == "x")
    ctx.facts.add(
        FactInfo(
            name="send_email",
            markers=("trusted",),
            args={
                "addr": types.str.val("bob", ctx=ctx),
                "msg": types.str.val("y", ctx=ctx),
            },
            cond=types.bool.val(True, ctx=ctx),
        )
    )

    # Every value in the fact is concrete, so pin the exact conclusion. A
    # satisfiability check on ``Not(spec)`` would still pass if the formula
    # ever grew a free variable while the contract held vacuously (gh-169).
    assert z3.simplify(spec.solve(ctx=ctx).expr).eq(z3.BoolVal(False))


def test_shared_first_line_with_multiline_neighbour() -> None:
    ctx = _ctx()
    # Two lambdas starting on one line where the second wraps onto the next:
    # their source spans are disjoint but overlap in line range, so matching
    # must compare (line, column) pairs rather than lines and columns apart.
    # The layout is the fixture here, so it is held against the formatter.
    make = LambdaSpec.from_callable
    # fmt: off
    first, second = make(lambda e: e.a == "1"), make(lambda e: e.b == "2"
                                                     and e.c == "3")
    # fmt: on

    row: dict[str, ProxySort] = {
        "a": types.str.val("1", ctx=ctx),
        "b": types.str.val("2", ctx=ctx),
        "c": types.str.val("nope", ctx=ctx),
    }

    assert z3.simplify(first.evaluate(row=row, ctx=ctx).expr) == z3.BoolVal(True)
    # The wrapped ``and e.c == "3"`` clause must survive and refute the row.
    assert z3.simplify(second.evaluate(row=row, ctx=ctx).expr) == z3.BoolVal(False)


def test_indistinguishable_lambda_fails_closed() -> None:
    # A lambda nested inside another on the same line: every instruction of the
    # inner code object also lies inside the outer lambda's source span, so no
    # unique node can be identified. Refuse rather than guess a body.
    inner = (lambda e: lambda f: f.msg == "x")(None)

    with pytest.raises(UnsupportedError, match="unambiguously"):
        LambdaSpec.from_callable(inner)


def test_def_predicate_reports_not_found_not_ambiguity() -> None:
    # gh-167: a ``def`` has no ``ast.Lambda`` on its first line. That is a
    # not-found, and must not be reported with the ambiguity wording.
    def predicate(e: object) -> bool:
        return True

    with pytest.raises(UnsupportedError, match="could not find lambda source"):
        LambdaSpec.from_callable(predicate)


def test_sourceless_lambda_is_unsupported_not_oserror() -> None:
    # gh-167: ``inspect.findsource`` raises OSError for exec-defined code. That
    # must become UnsupportedError so it surfaces as SKIP rather than escaping
    # Theorem.prove() as a traceback.
    namespace: dict[str, object] = {}
    exec('f = lambda e: e.addr == "a"', namespace)  # noqa: S102

    with pytest.raises(UnsupportedError, match="could not read the source"):
        LambdaSpec.from_callable(namespace["f"])  # type: ignore[arg-type]


def test_no_debug_ranges_is_named_in_the_refusal(tmp_path: Path) -> None:
    # gh-167: without column positions every same-line candidate survives
    # span filtering; the message must blame the interpreter flag, not the
    # contract. Run in a subprocess because the flag is process-wide.
    script = tmp_path / "probe.py"
    script.write_text(
        "from clauz3.spec import LambdaSpec\n"
        "from clauz3._vendor.deal_solver._exceptions import UnsupportedError\n"
        'f = lambda e: e.addr == "a"; g = lambda e: e.msg == "b"  # noqa: E731\n'
        "try:\n"
        "    LambdaSpec.from_callable(f)\n"
        "except UnsupportedError as exc:\n"
        "    print(exc)\n"
        "# A lambda alone on its line is still located by line number.\n"
        'alone = lambda e: e.addr == "c"  # noqa: E731\n'
        'print("ALONE:", type(LambdaSpec.from_callable(alone).body).__name__)\n'
    )
    src = Path(__file__).resolve().parents[1] / "src"
    existing = os.environ.get("PYTHONPATH")
    pythonpath = f"{src}{os.pathsep}{existing}" if existing else str(src)
    result = subprocess.run(
        [sys.executable, "-X", "no_debug_ranges", str(script)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": pythonpath},
    )
    assert result.returncode == 0, result.stderr
    assert "no_debug_ranges" in result.stdout, result.stdout
    assert "ALONE: Compare" in result.stdout, result.stdout

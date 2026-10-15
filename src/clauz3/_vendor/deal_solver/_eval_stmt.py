from __future__ import annotations

import typing

import astroid
import z3

from clauz3.diagnostics import proof as proof_monitor

from ._ast import infer
from ._context import Context, ExceptionInfo, ReturnInfo
from ._context._branch import merge_branch_layers
from ._context._trace import BOOLEAN_OPERAND, GUARDED_EXPRESSION, QUANTIFIED_LOOP_BODY
from ._eval_expr import eval_expr
from ._exceptions import UnsupportedError
from ._proxies import FuncSort, NoneSort, if_expr, or_expr, types
from ._registry import HandlersRegistry


eval_stmt: HandlersRegistry[None] = HandlersRegistry()


def eval_func(node: astroid.FunctionDef, ctx: Context) -> None:
    """Execute a function body in its call context."""
    if node.name in ctx.trace:
        raise UnsupportedError("recursive calls are not supported")

    with ctx.trace.guard(node.name):
        for statement in node.body:  # pragma: no cover
            eval_stmt(node=statement, ctx=ctx)


@eval_stmt.register(astroid.FunctionDef)
def eval_func_def(node: astroid.FunctionDef, ctx: Context) -> None:
    """A nested function declaration does not execute its body."""
    from ._ownership import capture_names

    capture_names(node, ctx=ctx)
    ctx.scope.set(name=node.name, value=FuncSort(node))


@eval_stmt.register(astroid.Assert)
def eval_assert(node: astroid.Assert, ctx: Context) -> None:
    assert node.test is not None, "assert without condition"
    expr = eval_expr(node=node.test, ctx=ctx)
    expr = expr.m_bool(ctx=ctx)
    expr = or_expr(ctx.interrupted, expr, ctx=ctx)
    if proof_monitor.active():
        proof_monitor.remember(
            expr.expr, **proof_monitor.location(node),
            predicate=node.test.as_string()[:300],
        )
    ctx.expected.add(expr)


@eval_stmt.register(astroid.Expr)
def eval_expr_stmt(node: astroid.Expr, ctx: Context) -> None:
    eval_expr(node=node.value, ctx=ctx)


@eval_stmt.register(astroid.Assign)
def eval_assign(node: astroid.Assign, ctx: Context) -> None:
    assert node.targets
    # Evaluate the right-hand side once: ``a = b = trusted()`` performs one
    # call, so it must record one fact and one set of obligations (#131).
    value_ref = eval_expr(node=node.value, ctx=ctx)
    for target in node.targets:
        # set item
        if isinstance(target, astroid.Subscript):
            if BOOLEAN_OPERAND in ctx.trace:
                raise UnsupportedError(
                    'subscript assignment inside a boolean operand is not supported',
                    target.as_string(),
                )
            # This direct m_setitem path bypasses Method.m_call. Refuse the
            # source-level write, not m_setitem itself: dictionary literals
            # also use that operation to construct a fresh value safely.
            if QUANTIFIED_LOOP_BODY in ctx.trace:
                raise UnsupportedError(
                    "subscript assignment inside a quantified loop is not supported",
                    target.as_string(),
                )
            if any(ctx.quantifiers):
                raise UnsupportedError('container assignment inside a symbolic loop')
            if GUARDED_EXPRESSION in ctx.trace:
                raise UnsupportedError(
                    'subscript assignment inside a guarded expression is not supported',
                    target.as_string(),
                )
            if isinstance(target.slice, astroid.Slice):
                raise UnsupportedError("cannot set item for slice")
            if not isinstance(target.value, astroid.Name):
                raise UnsupportedError(
                    "subscript assignment requires a named receiver",
                    target.as_string(),
                )
            from ._mutation_bindings import require_local_mutation_receiver

            require_local_mutation_receiver(target.value)
            key_ref = eval_expr(node=target.slice, ctx=ctx)
            target_ref = eval_expr(node=target.value, ctx=ctx)
            if isinstance(key_ref, NoneSort) or isinstance(value_ref, NoneSort):
                raise UnsupportedError('unsupported operation on None', 'item assignment')
            from ._proxies._list import TrustedStringListSort
            if isinstance(target_ref, TrustedStringListSort):
                raise UnsupportedError('cannot mutate a trusted string-list return')
            ctx.ownership.share(value_ref)
            ctx.ownership.require_write(target_ref, target.value.name, ctx=ctx)
            new_value = target_ref.m_setitem(key_ref, value_ref, ctx=ctx)
            ctx.ownership.replace(target_ref, new_value)
            ctx.scope.set(name=target.value.name, value=new_value)
            continue
        # assign to a variable
        if isinstance(target, astroid.AssignName):
            ctx.ownership.bind(target.name, value_ref, ctx=ctx)
            ctx.scope.set(name=target.name, value=value_ref)
            continue
        raise UnsupportedError("cannot assign to", type(target).__name__)


@eval_stmt.register(astroid.Return)
def eval_return(node: astroid.Return, ctx: Context) -> None:
    value = eval_expr(node=node.value, ctx=ctx)
    ctx.ownership.share(value)
    ctx.returns.add(
        ReturnInfo(
            value=value,
            cond=ctx.interrupted.m_not(ctx=ctx),
        )
    )


@eval_stmt.register(astroid.If)
def eval_if_else(node: astroid.If, ctx: Context) -> None:
    assert node.test
    assert node.body

    test_ref = eval_expr(node=node.test, ctx=ctx)

    ctx_then = ctx.make_child()
    for subnode in node.body:
        eval_stmt(node=subnode, ctx=ctx_then)
    ctx_else = ctx.make_child()
    for subnode in node.orelse or []:
        eval_stmt(node=subnode, ctx=ctx_else)

    # update variables
    changed_vars = set(ctx_then.scope.layer) | set(ctx_else.scope.layer)
    for var_name in changed_vars:
        val_then = ctx_then.scope.get(name=var_name)
        val_else = ctx_else.scope.get(name=var_name)
        if val_then is None or val_else is None:
            continue
        value = if_expr(test_ref, val_then, val_else, ctx=ctx)
        ctx.scope.set(name=var_name, value=value)

    # The branch's assumptions and obligations apply only where it ran;
    # exceptional exits, returns, and trusted facts carry the same guard.
    guard = test_ref.m_bool(ctx=ctx)
    merge_branch_layers(branch=ctx_then, guard=guard, ctx=ctx)
    merge_branch_layers(branch=ctx_else, guard=guard.m_not(ctx=ctx), ctx=ctx)


@eval_stmt.register(astroid.Raise)
def eval_raise(node: astroid.Raise, ctx: Context) -> None:
    names: set[str] = set()
    for exc in (node.exc, node.cause):
        if exc is None:
            continue
        names.update(_get_all_bases(exc))
    ctx.exceptions.add(
        ExceptionInfo(
            name=next(_get_all_bases(node.exc)),
            names=names,
            cond=ctx.interrupted.m_not(ctx=ctx),
        )
    )


def _get_all_bases(node) -> typing.Iterator[str]:
    def_nodes = infer(node)
    for def_node in def_nodes:
        if isinstance(def_node, astroid.Instance):
            def_node = def_node._proxied
        if isinstance(node, astroid.Name):
            yield node.name

        if not isinstance(def_node, astroid.ClassDef):
            continue
        yield def_node.name
        for parent_node in def_node.bases:
            if isinstance(parent_node, astroid.Name):
                yield from _get_all_bases(parent_node)


@eval_stmt.register(astroid.Global)
@eval_stmt.register(astroid.ImportFrom)
@eval_stmt.register(astroid.Import)
@eval_stmt.register(astroid.Pass)
def eval_skip(node, ctx: Context) -> None:
    pass


@eval_stmt.register(astroid.For)
def eval_for(node: astroid.For, ctx: Context) -> None:
    from ._context._quantifier import Quantifier
    from ._proxies._row import QueryResultSort
    from ._proxies._list import TrustedStringListSort

    if node.orelse:
        raise UnsupportedError("for-else is not supported in v1")
    if not isinstance(node.target, astroid.AssignName):
        raise UnsupportedError(
            "tuple-unpack in for-loops is not supported in v1; "
            "trusted layers should return Row-shaped rows"
        )

    # v1: reject break/continue/return inside the loop body
    def _walk_subnodes(nodes):
        for n in nodes:
            yield n
            for child in n.get_children():
                yield from _walk_subnodes([child])

    for sub in _walk_subnodes(node.body):
        if isinstance(sub, (astroid.Break, astroid.Continue, astroid.Return)):
            raise UnsupportedError(
                f"{type(sub).__name__.lower()} inside a for-loop is not "
                f"supported in v1"
            )

    literal_range_items = _literal_range_items(node.iter, ctx=ctx)
    if literal_range_items is not None:
        _eval_unrolled_for(node=node, values=literal_range_items, ctx=ctx)
        return

    literal_sequence_items = _literal_sequence_items(node.iter, ctx=ctx)
    if literal_sequence_items is not None:
        _eval_unrolled_for(node=node, values=literal_sequence_items, ctx=ctx)
        return

    # Query results are the symbolic quantified case.
    # Literal lists/tuples and range(N) were handled above by concrete unrolling.
    iterable = eval_expr(node.iter, ctx=ctx)
    if not isinstance(iterable, (QueryResultSort, TrustedStringListSort)):
        raise UnsupportedError(
            f"for-loops can only iterate over trusted list[Row] or list[str] "
            f"returns, literal lists/tuples, or range(N) with a literal integer "
            f"in v1; got {type(iterable).__name__}"
        )
    if isinstance(iterable, TrustedStringListSort) and (
        iterable.iteration_origin is None
        or not iterable.expr.eq(iterable.iteration_origin)
    ):
        raise UnsupportedError(
            'symbolic iteration requires an unchanged trusted string-list return'
        )

    _reject_loop_carried_state(node, ctx=ctx)

    # Fresh symbolic index variable
    i_var = z3.FreshInt(f"loop_idx_{node.lineno}", ctx=ctx.z3_ctx)
    quantifier = Quantifier(
        bound_var=i_var,
        source=iterable,
        lower=z3.IntVal(0),
        upper=(
            iterable.length_expr if isinstance(iterable, QueryResultSort)
            else z3.Length(iterable.expr)
        ),
    )

    # The body gets its own scope layer, so a name it binds does not outlive
    # the loop. The quantified path runs the body once, for one symbolic row,
    # under a quantifier that is popped below: after the loop that value stands
    # for no particular iteration, and on the zero-row path the binding never
    # happened at all. Leaking it let ``for r in rows: addr = r.email`` followed
    # by ``send_email(addr, ...)`` prove, and Python leaks the loop variable the
    # same way. Dropping both is the fail-closed reading -- a later read raises
    # "cannot resolve name" from ``eval_name`` and skips the proof.
    body_ctx = ctx.make_child()

    # Bind loop variable to iterable.at(i)
    item_proxy = iterable.at(i_var, ctx=ctx)
    body_ctx.scope.set(name=node.target.name, value=item_proxy)

    # Push quantifier, evaluate body, pop quantifier
    body_ctx.quantifiers.add(quantifier)
    try:
        # The body scope is discarded. A shared trace guard follows methods
        # and item assignments through branches and detached helper frames,
        # whose writes would otherwise be lost too. Iterable evaluation and
        # independently unrolled loops remain outside this guard.
        with ctx.trace.guard(QUANTIFIED_LOOP_BODY):
            for stmt in node.body:
                eval_stmt(stmt, ctx=body_ctx)
    finally:
        body_ctx.quantifiers.layer.pop()

    bounds = quantifier.bounds_expr()
    for assumption in body_ctx.given.layer:
        ctx.given.add(types.bool(z3.ForAll(i_var, z3.Implies(bounds, assumption.expr))))
    for expected in body_ctx.expected.layer:
        wrapped = types.bool(z3.ForAll(i_var, z3.Implies(bounds, expected.expr)))
        proof_monitor.inherit(expected.expr, wrapped.expr)
        ctx.expected.add(wrapped)
    for fact in body_ctx.facts.layer:
        ctx.facts.add(fact)
    for exc in body_ctx.exceptions.layer:
        reached = types.bool(z3.Exists(i_var, z3.And(bounds, exc.cond.expr)))
        # Symbolic loops must be exception-free even when an embedding caller
        # declares @deal.raises. We do not model which iteration aborts a loop.
        ctx.expected.add(reached.m_not(ctx=ctx))
        ctx.exceptions.add(exc._replace(cond=reached))


def _reject_loop_carried_state(node: astroid.For, ctx: Context) -> None:
    """Refuse a quantified loop whose body carries state between iterations.

    The quantified path evaluates the body exactly once, for a single symbolic
    index, and every fact it emits is generalised over that index. A body that
    only reads the current row is faithful under that model. A body that
    *writes* a name it can see from a previous iteration is not: the write is
    modelled as happening once, so a counter is always ``1``, an accumulator
    holds one row's contribution rather than the sum, and a rebound outer name
    keeps its first-iteration value -- including on the zero-row path, where
    the loop body does not run at all.

    Modelling the accumulation properly means recurrences the quantified
    encoding has no vocabulary for, so v1 refuses instead, as
    ``docs/superpowers/specs/2026-05-25-symbolic-iteration-design.md``
    ("For-loop handler semantics") already specified and this handler did not
    enforce. See issue #123.

    The unrolled paths above are unaffected: concrete iteration re-evaluates
    the body per element, which models loop-carried state exactly.
    """
    assert isinstance(node.target, astroid.AssignName)
    if ctx.scope.get(node.target.name) is not None:
        raise UnsupportedError('symbolic loop target shadows an existing binding', node.target.name)
    for assigned in _body_bindings(node):
        if assigned.name == node.target.name:
            raise UnsupportedError(
                "reassigning the loop variable",
                repr(assigned.name),
                "inside a for-loop over a trusted query is not supported in "
                "v1: the body is evaluated once for a symbolic row, so the "
                "rebinding would be modelled as a single iteration",
            )
        if ctx.scope.get(assigned.name) is not None:
            raise UnsupportedError(
                "assigning to",
                repr(assigned.name),
                "-- a name bound outside the loop -- inside a for-loop over a "
                "trusted query is not supported in v1: the body is evaluated "
                "once for a symbolic row, so counters and accumulators would "
                "be modelled as a single iteration",
            )


def _body_bindings(node: astroid.For) -> typing.Iterator[astroid.AssignName]:
    """Every name the loop body binds in the scope that encloses the loop.

    Nested loops and branches count -- an accumulator one level in is still an
    accumulator. Comprehension targets and the bodies of nested ``def``/``class``
    statements do not: they bind in their own scope and cannot be read by the
    next iteration, which is what ``scope()`` distinguishes.
    """
    scope = node.scope()
    for stmt in node.body:
        for assigned in stmt.nodes_of_class(astroid.AssignName):
            if assigned.scope() is scope:
                yield assigned


def _literal_range_items(node: astroid.NodeNG, *, ctx: Context) -> list | None:
    if not isinstance(node, astroid.Call):
        return None
    if not isinstance(node.func, astroid.Name) or node.func.name != "range":
        return None
    inferred = infer(node.func)
    if not any(
        isinstance(defn, astroid.ClassDef) and defn.qname() == "builtins.range"
        for defn in inferred
    ):
        return None
    if node.keywords or len(node.args) != 1:
        raise UnsupportedError(
            "for-loops over range() only support range(N) with a literal "
            "integer in v1"
        )
    bound = node.args[0]
    if not isinstance(bound, astroid.Const) or not isinstance(bound.value, int):
        raise UnsupportedError(
            "for-loops over range() only support range(N) with a literal "
            "integer in v1"
        )
    return [types.int.val(i, ctx=ctx) for i in range(max(bound.value, 0))]


def _literal_sequence_items(node: astroid.NodeNG, *, ctx: Context) -> list | None:
    if not isinstance(node, (astroid.List, astroid.Tuple)):
        return None
    values = [eval_expr(item, ctx=ctx) for item in node.elts]
    # The implicit iterable retains every item, including repeated references
    # when the target reuses the original variable's name.
    for value in values:
        ctx.ownership.share(value)
    return values


def _eval_unrolled_for(
    *,
    node: astroid.For,
    values: list,
    ctx: Context,
) -> None:
    assert isinstance(node.target, astroid.AssignName)
    for value in values:
        ctx.ownership.bind(node.target.name, value, ctx=ctx)
        ctx.scope.set(name=node.target.name, value=value)
        for stmt in node.body:
            eval_stmt(stmt, ctx=ctx)

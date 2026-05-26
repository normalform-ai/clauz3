from __future__ import annotations

import typing

import astroid
import z3

from ._annotations import ann2type
from ._ast import infer
from ._context import Context, ExceptionInfo, FactInfo, ReturnInfo
from ._eval_expr import eval_expr
from ._exceptions import UnsupportedError
from ._proxies import if_expr, or_expr, types
from ._registry import HandlersRegistry


eval_stmt: HandlersRegistry[None] = HandlersRegistry()


@eval_stmt.register(astroid.FunctionDef)
def eval_func(node: astroid.FunctionDef, ctx: Context) -> None:
    # if it is a recursive call, fake the function
    if node.name in ctx.trace:
        args = [v.expr for v in ctx.scope.layer.values()]
        # generate function signature
        sorts = [arg.sort() for arg in args]
        assert node.returns, "cannot find type annotation for already executed func"
        sort = ann2type(name="_", node=node.returns, ctx=ctx.z3_ctx)
        assert sort is not None, "cannot eval type annotation for already executed func"
        sorts.append(sort.sort())

        func = z3.Function(node.name, *sorts)
        proxy = type(sort)
        ctx.returns.add(
            ReturnInfo(
                value=proxy(func(*args)),
                cond=types.bool.val(True, ctx=ctx),
            )
        )
        return

    # otherwise, try to execute it
    with ctx.trace.guard(node.name):
        try:
            for statement in node.body:  # pragma: no cover
                eval_stmt(node=statement, ctx=ctx)
        except UnsupportedError as exc:
            ctx.skips.append(exc)


@eval_stmt.register(astroid.Assert)
def eval_assert(node: astroid.Assert, ctx: Context) -> None:
    assert node.test is not None, "assert without condition"
    expr = eval_expr(node=node.test, ctx=ctx)
    expr = expr.m_bool(ctx=ctx)
    expr = or_expr(ctx.interrupted, expr, ctx=ctx)
    ctx.expected.add(expr)


@eval_stmt.register(astroid.Expr)
def eval_expr_stmt(node: astroid.Expr, ctx: Context) -> None:
    eval_expr(node=node.value, ctx=ctx)


@eval_stmt.register(astroid.Assign)
def eval_assign(node: astroid.Assign, ctx: Context) -> None:
    assert node.targets
    for target in node.targets:
        value_ref = eval_expr(node=node.value, ctx=ctx)
        # set item
        if isinstance(target, astroid.Subscript):
            if isinstance(target.slice, astroid.Slice):
                raise UnsupportedError("cannot set item for slice")
            key_ref = eval_expr(node=target.slice, ctx=ctx)
            target_ref = eval_expr(node=target.value, ctx=ctx)
            new_value = target_ref.m_setitem(key_ref, value_ref, ctx=ctx)
            if isinstance(target.value, astroid.Name):
                ctx.scope.set(name=target.value.name, value=new_value)
                continue
        # assign to a variable
        if isinstance(target, astroid.AssignName):
            ctx.scope.set(name=target.name, value=value_ref)
            continue
        raise UnsupportedError("cannot assign to", type(target).__name__)


@eval_stmt.register(astroid.Return)
def eval_return(node: astroid.Return, ctx: Context) -> None:
    ctx.returns.add(
        ReturnInfo(
            value=eval_expr(node=node.value, ctx=ctx),
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

    # update new assertions
    true = types.bool.val(True, ctx=ctx)
    for constr in ctx_then.expected.layer:
        ctx.expected.add(if_expr(test_ref, constr, true, ctx=ctx))
    for constr in ctx_else.expected.layer:
        ctx.expected.add(if_expr(test_ref, true, constr, ctx=ctx))

    # update new exceptions
    false = types.bool.val(False, ctx=ctx)
    for exc in ctx_then.exceptions.layer:
        ctx.exceptions.add(
            ExceptionInfo(
                name=exc.name,
                names=exc.names,
                cond=if_expr(test_ref, exc.cond, false, ctx=ctx),
                message=exc.message,
            )
        )
    for exc in ctx_else.exceptions.layer:
        ctx.exceptions.add(
            ExceptionInfo(
                name=exc.name,
                names=exc.names,
                cond=if_expr(test_ref, false, exc.cond, ctx=ctx),
                message=exc.message,
            )
        )

    # update new return statements
    false = types.bool.val(False, ctx=ctx)
    for ret in ctx_then.returns.layer:
        ctx.returns.add(
            ReturnInfo(
                value=ret.value,
                cond=if_expr(test_ref, ret.cond, false, ctx=ctx),
            )
        )
    for ret in ctx_else.returns.layer:
        ctx.returns.add(
            ReturnInfo(
                value=ret.value,
                cond=if_expr(test_ref, false, ret.cond, ctx=ctx),
            )
        )

    # update new trusted invocation facts
    for fact in ctx_then.facts.layer:
        ctx.facts.add(
            FactInfo(
                name=fact.name,
                markers=fact.markers,
                args=fact.args,
                cond=if_expr(test_ref, fact.cond, false, ctx=ctx),
                quantifiers=fact.quantifiers,
            )
        )
    for fact in ctx_else.facts.layer:
        ctx.facts.add(
            FactInfo(
                name=fact.name,
                markers=fact.markers,
                args=fact.args,
                cond=if_expr(test_ref, false, fact.cond, ctx=ctx),
                quantifiers=fact.quantifiers,
            )
        )


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
    if not isinstance(iterable, QueryResultSort):
        raise UnsupportedError(
            f"for-loops can only iterate over list[Row]-returning trusted "
            f"calls, literal lists/tuples, or range(N) with a literal integer "
            f"in v1; got {type(iterable).__name__}"
        )

    # Fresh symbolic index variable
    idx_name = f"loop_idx_{node.lineno}_{id(ctx)}"
    i_var = z3.Int(idx_name)
    quantifier = Quantifier(
        bound_var=i_var,
        source=iterable,
        lower=z3.IntVal(0),
        upper=iterable.length_expr,
    )

    # Bind loop variable to iterable.at(i)
    row_proxy = iterable.at(i_var, ctx=ctx)
    ctx.scope.set(name=node.target.name, value=row_proxy)

    # Push quantifier, evaluate body, pop quantifier
    ctx.quantifiers.add(quantifier)
    try:
        for stmt in node.body:
            eval_stmt(stmt, ctx=ctx)
    finally:
        ctx.quantifiers.layer.pop()


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
    return [eval_expr(item, ctx=ctx) for item in node.elts]


def _eval_unrolled_for(
    *,
    node: astroid.For,
    values: list,
    ctx: Context,
) -> None:
    assert isinstance(node.target, astroid.AssignName)
    for value in values:
        ctx.scope.set(name=node.target.name, value=value)
        for stmt in node.body:
            eval_stmt(stmt, ctx=ctx)

from __future__ import annotations

import typing

import astroid
import z3

from clauz3.diagnostics import proof as proof_monitor

from ._context import Context, Scope
from ._context._branch import merge_branch_layers
from ._context._layer import Layer
from ._eval_expr import eval_expr
from ._exceptions import UnsupportedError
from ._goal import Goal
from ._proxies import BoolSort, ProxySort, and_expr, or_expr
from ._types import AstNode


# This safety check has its own fixed budget, independent of theorem timeout.
_POST_RETURN_COVERAGE_TIMEOUT_MS = 1_000


class Contract(typing.NamedTuple):
    """
    + `name` can be `pre`, `post`, or `raises`. Everything else is ignored.
    + `args` contains one node for `pre` and `post` (which is the validator)
      and many nodes for `raises` (which are exceptions).
    """
    name: str
    args: list[AstNode]


class Contracts(typing.NamedTuple):
    pre: Goal
    post: Goal
    raises: set[str]


def eval_contracts(
    func: astroid.FunctionDef, ctx: Context, *, completed: bool,
) -> Contracts:
    declarations = tuple(ctx.get_contracts(func))
    if completed and any(c.name in {'post', 'ensure'} for c in declarations):
        _require_modeled_post_return(func=func, ctx=ctx)
    goals = Contracts(
        pre=Goal(),
        post=Goal(),
        raises=set(),
    )
    for contract in declarations:
        if contract.name == 'pre':
            value = _eval_pre(ctx=ctx, args=contract.args)
            if value is None:
                continue
            goals.pre.add(value)
        if contract.name == 'post':
            for value in _eval_post(ctx=ctx, args=contract.args):
                goals.post.add(value)
        if contract.name == 'ensure':
            for value in _eval_ensure(ctx=ctx, args=contract.args):
                goals.post.add(value)
        if contract.name == 'raises':
            values = _eval_raises(ctx=ctx, args=contract.args)
            goals.raises.update(values)
    return goals


def _require_modeled_post_return(*, func: astroid.FunctionDef, ctx: Context) -> None:
    # A postcondition runs on every normal completion, including implicit
    # None. Empty/partial return layers cannot silently omit those calls.
    # This is only a completion-phase check: trusted preconditions are also
    # evaluated before the annotated return has been materialized.
    covered = z3.simplify(ctx.interrupted.expr)
    if z3.is_true(covered):
        return
    if not z3.is_false(covered):
        solver = z3.Solver(ctx=ctx.z3_ctx)
        solver.set('timeout', proof_monitor.solver_timeout_ms(_POST_RETURN_COVERAGE_TIMEOUT_MS))
        # Only return/raise path predicates participate. Caller assumptions
        # or the postcondition itself cannot justify a missing modeled result.
        solver.add(z3.Not(covered))
        if solver.check() == z3.unsat:
            return
    raise UnsupportedError(
        'postcondition requires a modeled return on every normal completion path',
        func.name,
    )


def _eval_pre(ctx: Context, args: list) -> BoolSort | None:
    contract = args[0]
    if not isinstance(contract, astroid.Lambda):
        return None
    assert contract.args
    return _eval_predicate(
        node=contract.body, ctx=ctx, guard=BoolSort.val(True, ctx=ctx),
    ).m_bool(ctx=ctx)


def _eval_predicate(*, node, ctx: Context, guard: BoolSort) -> ProxySort:
    # A postcondition on an unreachable return never runs, including any
    # nested helper conditions that would otherwise refuse during evaluation.
    if z3.is_false(z3.simplify(guard.expr)):
        return BoolSort.val(True, ctx=ctx)
    # A predicate runs after (or before) the function, not after its return
    # statement within the same execution. Start its own completion state so
    # a completed function return cannot suppress the predicate's live RHS.
    predicate_ctx = ctx.make_child().evolve(returns=Layer(), exceptions=Layer())
    value = eval_expr(node=node, ctx=predicate_ctx)
    # Contract extraction can visit predicates more than once (for example
    # before and after materializing a trusted return). Recording their calls
    # as runtime events would invent duplicates; predicates remain pure here.
    if predicate_ctx.facts.layer:
        raise UnsupportedError('trusted calls inside condition predicates are not supported')
    # Conditions are evaluated in detached callee contexts as well as theorem
    # contexts. A returned fallback value must not hide a predicate exception
    # when its caller later copies only the condition's proposition. Check
    # before merging assumptions: the predicate's own value (or a callee's
    # postcondition) cannot establish that its evaluation completed safely.
    for exc in predicate_ctx.exceptions.layer:
        active = and_expr(guard, exc.cond, ctx=predicate_ctx)
        if not z3.is_false(z3.simplify(active.expr)):
            raise UnsupportedError('condition predicate may raise', exc.name, exc.message)
    # A nested helper assertion or precondition is a proof obligation rather
    # than an ExceptionInfo, but can abort the predicate in the same way.
    for expected in predicate_ctx.expected.layer:
        safe = or_expr(guard.m_not(ctx=predicate_ctx), expected, ctx=predicate_ctx)
        if not z3.is_true(z3.simplify(safe.expr)):
            raise UnsupportedError('condition predicate has an unchecked proof obligation')
    merge_branch_layers(branch=predicate_ctx, guard=guard, ctx=ctx)
    return value


def _eval_post(ctx: Context, args: list) -> typing.Iterator[BoolSort]:
    contract = args[0]
    if not isinstance(contract, astroid.Lambda):
        return
    assert contract.args
    cargs = contract.args.arguments
    ctx = ctx.evolve(scope=Scope.make_empty())
    for ret in ctx.returns:
        ctx.scope.set(
            name=cargs[0].name,
            value=ret.value,
        )
        # The contract is valid if the return value is not reached
        # or it passed the post-condition test.
        yield or_expr(
            ret.cond.m_not(ctx=ctx),
            _eval_predicate(node=contract.body, ctx=ctx, guard=ret.cond),
            ctx=ctx,
        )


def _eval_ensure(ctx: Context, args: list) -> typing.Iterator[BoolSort]:
    contract = args[0]
    if not isinstance(contract, astroid.Lambda):
        return
    assert contract.args
    # Only the result name needs a local binding. Keep the outer obligation
    # layers so predicate errors survive after this generator is exhausted.
    ctx = ctx.evolve(scope=ctx.scope.make_child())
    for ret in ctx.returns:
        ctx.scope.set(
            name='result',
            value=ret.value,
        )
        yield or_expr(
            ret.cond.m_not(ctx=ctx),
            _eval_predicate(node=contract.body, ctx=ctx, guard=ret.cond),
            ctx=ctx,
        )


def _eval_raises(ctx: Context, args: list) -> typing.Iterator[str]:
    for arg in args:
        if isinstance(arg, astroid.Name):
            yield arg.name

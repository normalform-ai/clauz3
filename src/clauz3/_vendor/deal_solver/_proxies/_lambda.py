from __future__ import annotations

import typing

import astroid

from .._context._trace import LAMBDA_BODY
from .._exceptions import UnsupportedError
from ._proxy import ProxySort


if typing.TYPE_CHECKING:
    from .._context import Context


def _has_lexical_capture(args: astroid.Arguments, body: astroid.NodeNG) -> bool:
    """Find enclosing local bindings, including those used by nested lambdas.

    Inspect lexical locals rather than filtered inference: a capture may be
    assigned after the lambda is created. Inner parameters and comprehension
    targets shadow outer locals, but defaults and the first comprehension
    iterable are evaluated outside their new scope.
    """
    boundary = args.parent
    comprehensions = (
        astroid.ListComp, astroid.SetComp, astroid.DictComp, astroid.GeneratorExp,
    )
    for name in body.nodes_of_class(astroid.Name):
        scope = name.scope()
        while not isinstance(scope, astroid.Module):
            outside = isinstance(scope, astroid.Lambda) and scope.args.parent_of(name)
            if isinstance(scope, comprehensions):
                iterable = scope.generators[0].iter
                outside = iterable is name or iterable.parent_of(name)
            if (
                not outside
                and isinstance(scope, (astroid.FunctionDef, astroid.Lambda, *comprehensions))
                and name.name in scope.locals
            ):
                if scope is not boundary and not boundary.parent_of(scope):
                    return True
                break
            if scope.parent is None:
                break
            scope = scope.parent.scope()
    return False


class LambdaSort(ProxySort):
    type_name = 'lambda'
    ctx: Context
    args: astroid.Arguments
    body: astroid.Expr
    has_captures: bool
    methods = ProxySort.methods.copy()

    def __init__(self, *, ctx: Context, args: astroid.Arguments, body) -> None:
        # Defaults execute at definition time, even when the lambda is never
        # called. Refuse declarations we cannot bind/evaluate before omitting
        # those effects or letting an unbound parameter read an outer local.
        if args.defaults or args.kwonlyargs or args.vararg or args.kwarg:
            raise UnsupportedError('unsupported lambda argument binding')
        self.ctx = ctx
        self.args = args
        self.body = body
        self.has_captures = _has_lexical_capture(args, body)

    @methods.add(name='__call__')
    def m_call(self, *values, ctx: Context, **kwargs) -> ProxySort:
        from .._eval_expr import eval_expr

        # eval_call injects var_name as dispatch metadata. Every actual lambda
        # parameter must be supplied positionally and bound exactly once.
        if len(values) != len(self.args.arguments) or kwargs.keys() - {'var_name'}:
            raise UnsupportedError('unsupported lambda argument binding')
        # The stored scope has current values only while it remains the active
        # scope. A branch can rebind a capture in a child, and a helper call has
        # a detached frame. Neither caller locals nor the stale defining scope
        # are a substitute for Python's shared closure cells.
        if self.has_captures and ctx.scope is not self.ctx.scope:
            raise UnsupportedError(
                'lambda capture from a different symbolic scope is not supported'
            )
        body_ctx = self.ctx.make_child().evolve(trace=ctx.trace)
        for arg, value in zip(self.args.arguments, values, strict=True):
            body_ctx.ownership.share(value)
            body_ctx.scope.set(name=arg.name, value=value)
        # Child scopes and detached helper frames share the active trace, so
        # mutating methods cannot hide a write in a discarded deeper scope.
        with body_ctx.trace.guard(LAMBDA_BODY):
            result = eval_expr(node=self.body, ctx=body_ctx)
        # Fail closed on trusted effects/preconditions in the lambda body. Like
        # the comprehension body (see _eval_expr._compr_reject_effects), this
        # child context's facts/expected/exceptions are never merged back into
        # the caller, so a guarantee over them would prove against an empty
        # trace while the effect runs at runtime — the same #67 unsoundness,
        # reached through a lambda. (Contract relation predicates do NOT use
        # this path; they are compiled symbolically via clauz3.spec.LambdaSpec.)
        raisable = [
            exc for exc in body_ctx.exceptions.layer if not exc.is_impossible
        ]
        if body_ctx.facts.layer or body_ctx.expected.layer or raisable:
            raise UnsupportedError(
                'trusted calls with effects or preconditions inside a lambda '
                'are not supported'
            )
        body_ctx.ownership.share(result)
        return result

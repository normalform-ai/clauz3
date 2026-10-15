from __future__ import annotations

import inspect
import itertools
import typing

import astroid
import z3

from clauz3.diagnostics import proof as proof_monitor

from .._exceptions import UnsupportedError
from ._funcs import and_expr, or_expr
from ._none import NoneSort
from ._proxy import ProxySort
from ._registry import types


if typing.TYPE_CHECKING:
    from .._context import Context


F = typing.Callable[..., ProxySort]


def _bind_registered_args(
    signature: inspect.Signature,
    args: tuple[ProxySort, ...],
    kwargs: dict[str, ProxySort],
) -> inspect.BoundArguments:
    # Python permits f(name=...) for f(name=default, /, **kwargs): the keyword
    # belongs to **kwargs, and the positional parameter keeps its default.
    # inspect.bind rejects that form on supported Python versions. Rename only
    # colliding positional-only parameters while validating; they are forwarded
    # positionally, so the original user keyword spelling stays untouched.
    parameters = list(signature.parameters.values())
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters):
        used_names = set(signature.parameters) | kwargs.keys()
        for index, parameter in enumerate(parameters):
            if (
                parameter.kind is inspect.Parameter.POSITIONAL_ONLY
                and parameter.name in kwargs
            ):
                replacement = f'_registered_positional_{index}'
                while replacement in used_names:
                    replacement += '_'
                used_names.add(replacement)
                parameters[index] = parameter.replace(name=replacement)
        signature = signature.replace(parameters=parameters)
    return signature.bind(*args, **kwargs)
_RETURN_COVERAGE_TIMEOUT_MS = 1_000


def _normal_completion_is_covered(ctx: Context) -> bool:
    # A normal completion is a path that neither returned nor raised. The
    # simplifier handles common cases, but is not a decision procedure even
    # for several early returns followed by an unconditional final return.
    interrupted = z3.simplify(ctx.interrupted.expr)
    if z3.is_true(interrupted):
        return True
    solver = z3.Solver(ctx=ctx.z3_ctx)
    solver.set('timeout', proof_monitor.solver_timeout_ms(_RETURN_COVERAGE_TIMEOUT_MS))
    # Only path predicates participate: contradictory callee assumptions must
    # never make a missing return appear covered. Unknown also fails closed.
    solver.add(z3.Not(interrupted))
    return solver.check() == z3.unsat


@types.add
class FuncSort(ProxySort):
    type_name = "function"
    impl: F
    methods = ProxySort.methods.copy()

    def __init__(self, impl: F) -> None:
        self.impl = impl

    @methods.add(name="__call__")
    def m_call(
        self, *args: ProxySort, ctx: Context, var_name=None, **kwargs: ProxySort
    ) -> ProxySort:
        """self(*args, **kwargs)"""
        if isinstance(self.impl, astroid.FunctionDef):
            return self._call_function(
                node=self.impl,
                ctx=ctx,
                call_args=args,
                call_kwargs=kwargs,
            )
        # Import after proxy initialization; the registry initializes builtin models.
        from .._funcs._registry import RegisteredFunction

        if not isinstance(self.impl, RegisteredFunction):
            raise UnsupportedError(
                'unsupported registered call argument binding: missing signature metadata',
            )
        registered = self.impl
        if registered.public_signature is None:
            raise UnsupportedError(
                'unsupported registered call argument binding: missing signature metadata',
                registered.name,
            )
        try:
            bound = _bind_registered_args(registered.public_signature, args, kwargs)
            # Catch incompatible model plumbing before execution. Do not catch
            # TypeError from the implementation itself as a binding failure.
            inspect.signature(registered.impl).bind(
                *bound.args, ctx=ctx, **bound.kwargs,
            )
        except (TypeError, ValueError) as exc:
            raise UnsupportedError(
                'unsupported registered call argument binding',
                registered.name,
                str(exc),
            ) from exc
        from .._funcs._builtins import builtins_bool, builtins_ignore

        if any(isinstance(value, NoneSort) for value in (*bound.args, *bound.kwargs.values())):
            if registered.impl not in (builtins_bool, builtins_ignore):
                raise UnsupportedError('unsupported operation on None')
        # Preserve omission: model defaults (and contract helper defaults) are
        # applied by their implementations, never materialized as Python values
        # in the symbolic argument list.
        return registered.impl(*bound.args, ctx=ctx, **bound.kwargs)

    @staticmethod
    def _call_function(
        node: astroid.FunctionDef,
        ctx: Context,
        call_args: tuple[ProxySort, ...],
        call_kwargs: dict[str, ProxySort],
    ) -> ProxySort:
        from .._eval_contracts import eval_contracts
        from .._eval_stmt import eval_func
        from .._context import ExceptionInfo, FactInfo

        trusted, markers = _trusted_boundary(node=node, ctx=ctx)
        bound_args = _bind_args(
            node=node,
            call_args=call_args,
            call_kwargs=call_kwargs,
            allow_omitted_defaults=trusted,
        )

        # put arguments into the scope.
        # `quantifiers` is a *child* of the caller's layer rather than empty, so
        # the enclosing for-loop frames stay visible to trusted calls made from
        # inside the callee. Without that parent link, an effect emitted in a
        # helper called from a query loop is recorded unquantified — as
        # happening once rather than once per row.
        func_ctx = ctx.make_empty(
            get_contracts=ctx.get_contracts,
            get_guarantees=ctx.get_guarantees,
            trace=ctx.trace,
            quantifiers=ctx.quantifiers.make_child(),
            ownership=ctx.ownership,
        )
        for name, value in bound_args.items():
            func_ctx.scope.set(name=name, value=value)

        if trusted:
            from ._list import contains_iteration_source

            if any(
                isinstance(arg, (types.list, types.tuple, types.dict, types.set))
                and contains_iteration_source(arg.factory)
                for arg in bound_args.values()
            ):
                raise UnsupportedError(
                    'passing a trusted string-list return to another effect is '
                    'not supported: the effect could mutate an iteration source'
                )
            if any(isinstance(arg, NoneSort) for arg in bound_args.values()):
                raise UnsupportedError('unsupported operation on None', 'trusted arguments')
            # clauz3 extension: @clauz3.trusted marks a trusted boundary
            # (as does a legacy @deal.has(...) carrying markers).
            # We require the trusted function preconditions, record the call
            # as a symbolic fact for guarantee solvers, and deliberately skip
            # the side-effecting function body. The body is what would have
            # produced the return value, so the value is materialized from the
            # return annotation instead.
            contracts = eval_contracts(func=node, ctx=func_ctx, completed=False)
            active_quantifiers = _active_quantifiers(ctx)
            pre_nodes = (
                [c.args[0] for c in ctx.get_contracts(node) if c.name == 'pre' and c.args]
                if proof_monitor.active() else []
            )
            for pre_index, pre in enumerate(contracts.pre):
                # Quantify at loop exit, after surrounding branch guards have
                # been attached. Quantifying here leaves those guards free.
                expected = or_expr(ctx.interrupted, pre, ctx=ctx)
                proof_monitor.remember(
                    expected.expr, effect=node.qname(),
                    predicate=(
                        pre_nodes[pre_index].as_string()[:300]
                        if pre_index < len(pre_nodes) else 'precondition'
                    ),
                )
                ctx.expected.add(expected)
            materialized = _materialize_trusted_return(
                node=node,
                bound_args=bound_args,
                ctx=ctx,
            )
            ctx.facts.add(
                FactInfo(
                    name=node.name,
                    markers=markers,
                    args=bound_args,
                    cond=ctx.interrupted.m_not(ctx=ctx),
                    quantifiers=active_quantifiers,
                    result=materialized,
                    qualified_name=node.qname(),
                )
            )
            if materialized is not None:
                # Trusted postconditions are constraints on materialized value.
                # Inject the materialized return into func_ctx so _eval_post
                # can bind `result` to it when evaluating the lambda.
                from .._context import ReturnInfo

                true_cond = types.bool.val(True, ctx=ctx)
                func_ctx.returns.add(ReturnInfo(value=materialized, cond=true_cond))
            # Complete the contract pass even for -> None. That annotation
            # supplies no modeled return here, so a postcondition must refuse
            # rather than disappear from the empty return layer.
            post_contracts = eval_contracts(func=node, ctx=func_ctx, completed=True)
            ctx.given.add(
                or_expr(
                    ctx.interrupted,
                    and_expr(*post_contracts.post, ctx=ctx),
                    ctx=ctx,
                )
            )
            if materialized is not None:
                return materialized
            # Keep None distinct from False/zero, while preserving its exact
            # false truthiness. FactInfo.result stays None for concrete replay.
            return NoneSort()

        # Ordinary helpers receive live collection references. Trusted calls
        # above instead cross marshal_out's deep-copy boundary, so do not
        # revoke their caller's ownership merely for passing an argument.
        for value in bound_args.values():
            ctx.ownership.share(value)

        # call the function
        eval_func(node=node, ctx=func_ctx)
        result = func_ctx.return_value
        if (
            result is not None
            and not isinstance(result, NoneSort)
            and not _normal_completion_is_covered(func_ctx)
        ):
            # return_value combines explicit returns only. A remaining normal
            # completion path produces None, which cannot be silently replaced
            # by an explicit value. Raised paths are already interrupted.
            raise UnsupportedError(
                'implicit None fallthrough with value returns is not supported: helper may implicitly return None',
                node.name,
            )

        # Everything crossing this boundary is conditional on the call actually
        # happening. `func_ctx` is detached, so its contents describe the callee
        # in isolation; if the caller has already returned or raised, none of it
        # occurred. An unguarded assumption is the dangerous direction — it can
        # discharge an assertion on a path where the call never ran.
        # Captured once, at call entry: the loop below adds the callee's own
        # exceptions to `ctx`, which would otherwise fold back into a re-read
        # `ctx.interrupted` and excuse the very preconditions this call owes.
        entry_interrupted = ctx.interrupted
        live = entry_interrupted.m_not(ctx=ctx)
        for fact in func_ctx.facts.layer:
            ctx.facts.add(
                FactInfo(
                    name=fact.name,
                    markers=fact.markers,
                    args=fact.args,
                    cond=and_expr(live, fact.cond.m_bool(ctx=ctx), ctx=ctx),
                    quantifiers=fact.quantifiers,
                    result=fact.result,
                    qualified_name=fact.qualified_name,
                )
            )
        for assumption in func_ctx.given.layer:
            ctx.given.add(or_expr(entry_interrupted, assumption, ctx=ctx))
        for constraint in func_ctx.expected.layer:
            expected = or_expr(entry_interrupted, constraint, ctx=ctx)
            proof_monitor.inherit(constraint.expr, expected.expr)
            ctx.expected.add(expected)
        for exc in func_ctx.exceptions.layer:
            ctx.exceptions.add(
                ExceptionInfo(
                    name=exc.name,
                    names=exc.names,
                    cond=and_expr(live, exc.cond.m_bool(ctx=ctx), ctx=ctx),
                    message=exc.message,
                )
            )

        # we ask pre-conditions to be true
        # and promise post-condition to be true
        contracts = eval_contracts(func=node, ctx=func_ctx, completed=True)
        ctx.expected.add(
            or_expr(entry_interrupted, and_expr(*contracts.pre, ctx=ctx), ctx=ctx)
        )
        ctx.given.add(
            or_expr(entry_interrupted, and_expr(*contracts.post, ctx=ctx), ctx=ctx)
        )

        if result is None:
            # No `return` on any path: the call evaluates to None, not False.
            return NoneSort()
        return result


def _active_quantifiers(ctx: "Context") -> tuple:
    """Walk the quantifiers Layer chain (parents first, current last).

    Equivalent to walking up the stack of for-loops that wrap the current
    point in symbolic execution.  Child contexts created inside if/else
    branches have a fresh Layer whose .parent points to the outer loop's
    Layer, so we must walk the whole chain to get all active quantifiers.
    """
    chain = []
    layer = ctx.quantifiers
    while layer is not None:
        chain.append(layer)
        layer = layer.parent
    # Reverse so outermost parent's items come first
    result = []
    for lyr in reversed(chain):
        result.extend(lyr.layer)
    return tuple(result)


def _bind_args(
    node: astroid.FunctionDef,
    call_args: tuple[ProxySort, ...],
    call_kwargs: dict[str, ProxySort],
    *,
    allow_omitted_defaults: bool = False,
) -> dict[str, ProxySort]:
    """Validate Python argument kinds while retaining explicit-call provenance.

    Defaults are marked only for arity checking; evaluating their AST at call
    time would not reproduce Python's definition-time default values. Trusted
    facts intentionally omit defaults, so a contract cannot mistake an omitted
    value for explicit intent. Ordinary bodies cannot use an incomplete scope.
    """
    arguments = node.args
    # Trusted functions run through Python's native binder, but ordinary
    # helpers use the subset interpreter, which refuses these parameter kinds.
    # Refuse during proof rather than approving a call that aborts mid-run.
    if not allow_omitted_defaults and (arguments.posonlyargs or arguments.kwonlyargs):
        raise UnsupportedError(
            "unsupported function argument binding: positional-only or "
            "keyword-only parameters in ordinary functions",
            node.name,
        )
    if arguments.vararg or arguments.kwarg:
        raise UnsupportedError(
            "unsupported function argument binding: variadic parameters",
            node.name,
        )

    posonly = arguments.posonlyargs or []
    positional = [*posonly, *(arguments.args or [])]
    first_default = len(positional) - len(arguments.defaults)
    default_present = object()
    parameters = []
    try:
        for index, arg in enumerate(positional):
            kind = (
                inspect.Parameter.POSITIONAL_ONLY
                if index < len(posonly)
                else inspect.Parameter.POSITIONAL_OR_KEYWORD
            )
            default = (
                default_present if index >= first_default else inspect.Parameter.empty
            )
            parameters.append(inspect.Parameter(arg.name, kind, default=default))
        for arg, default_node in zip(
            arguments.kwonlyargs, arguments.kw_defaults, strict=True
        ):
            default = (
                default_present if default_node is not None else inspect.Parameter.empty
            )
            parameters.append(
                inspect.Parameter(
                    arg.name, inspect.Parameter.KEYWORD_ONLY, default=default
                )
            )
        signature = inspect.Signature(parameters)
    except ValueError as exc:
        raise UnsupportedError(
            "unsupported function argument binding", node.name, str(exc)
        ) from exc

    try:
        bound = signature.bind(*call_args, **call_kwargs)
    except TypeError as exc:
        raise UnsupportedError(
            "unsupported function argument binding", node.name, str(exc)
        ) from exc
    if not allow_omitted_defaults and len(bound.arguments) != len(parameters):
        raise UnsupportedError(
            "unsupported function argument binding: omitted defaults", node.name
        )
    # Do not apply_defaults(): both FactInfo.args and query source provenance
    # must describe only arguments explicitly supplied by the caller.
    return dict(bound.arguments)


def _trusted_boundary(
    node: astroid.FunctionDef,
    ctx: Context,
) -> tuple[bool, tuple[str, ...]]:
    """Whether this function is a trusted effect, and the markers it declares.

    clauz3 extension. The two answers are separate because the two spellings
    decide the boundary differently:

    * ``effect`` (``@clauz3.trusted``) is a boundary by being there. It takes
      no arguments, so the fact it records carries no markers.
    * ``has`` (``@deal.has``) is a boundary only when it carries a marker,
      which is deal's own meaning: a bare ``@deal.has()`` declares the
      function *free* of side effects, so inlining it is right.

    Markers from either accumulate: they name the effect for ``effect("read")``
    relations and for a coverage policy's ``when_used``.
    """
    markers: list[str] = []
    trusted = False
    for contract in ctx.get_contracts(node):
        if contract.name not in {"has", "effect"}:
            continue
        trusted = trusted or contract.name == "effect"
        for arg in contract.args:
            if isinstance(arg, astroid.Const) and isinstance(arg.value, str):
                markers.append(arg.value)
    return trusted or bool(markers), tuple(markers)


_SCALAR_RETURN_COUNTER = itertools.count()
_QUERY_RETURN_COUNTER = itertools.count()


def _materialize_trusted_return(
    *,
    node: astroid.FunctionDef,
    bound_args: dict[str, ProxySort],
    ctx: Context,
) -> "ProxySort | None":
    """Materialize a symbolic value for a trusted function's return.

    The prover deliberately skips the trusted body, and that body is what would
    have produced the return value, so the value has to be invented from the
    return annotation. It spans the represented Python type's full domain:
    a concrete stand-in decides every branch that reads it at prove time,
    which is how a live effect gets proved unreachable. Postconditions on the
    result are applied by the caller and supply any additional constraints.

    ``-> None`` returns an internal sentinel so the caller supplies NoneSort. A ``list[<Row>]`` return becomes a
    QueryResultSort; every other annotation becomes a fresh variable of its
    sort, constrained only to Python's value domain. An annotation with no
    sort raises rather than guessing.
    """
    from .._annotations import ann2type
    from .._domains import add_value_domain

    returns = node.returns
    if returns is None:
        raise UnsupportedError(
            "missed return annotation for trusted function", node.name
        )
    if _is_none_annotation(returns):
        return None
    query = _materialize_query_result(node=node, bound_args=bound_args, ctx=ctx)
    if query is not None:
        return query
    uid = next(_SCALAR_RETURN_COUNTER)
    value = ann2type(name=f"{node.name}_result_{uid}", node=returns, ctx=ctx.z3_ctx)
    if value is None:
        raise UnsupportedError(
            "unsupported return annotation for trusted function",
            returns.as_string(),
        )
    from ._list import ListSort, TrustedStringListSort

    quantifiers = _active_quantifiers(ctx)
    if quantifiers:
        indices = [q.bound_var for q in quantifiers]
        result_function = z3.Function(
            f'{node.name}_result_{uid}_by_iteration',
            *[index.sort() for index in indices], value.expr.sort(),
        )
        value = value.factory.wrap(result_function(*indices))
    if isinstance(value, ListSort) and value.subtypes[0].type is types.str:
        value = TrustedStringListSort(
            expr=value.expr, subtypes=value.subtypes, iteration_origin=value.expr,
        )
    add_value_domain(value, ctx=ctx)
    return value


def _is_none_annotation(node: astroid.NodeNG) -> bool:
    """Return whether the annotation node spells ``None``."""
    if isinstance(node, astroid.Const):
        return node.value is None or node.value == "None"
    return isinstance(node, astroid.Name) and node.name == "None"


def _materialize_query_result(
    *,
    node: astroid.FunctionDef,
    bound_args: dict[str, ProxySort],
    ctx: Context,
) -> "ProxySort | None":
    """Materialize a ``list[<Row>]`` return as a symbolic query result.

    Covers a concrete ``list[SomeRow]`` return and a generic ``list[T]`` return
    whose Row schema comes from an argument. Returns None when the annotation
    names no Row schema, leaving the caller to model it by sort.
    """
    from ._row import QueryResultSort, z3_datatype_for_row
    from .._domains import add_value_domain

    returns = node.returns
    if not isinstance(returns, astroid.Subscript):
        return None
    if not isinstance(returns.value, astroid.Name) or returns.value.name != "list":
        return None
    inner = returns.slice

    schema = _row_schema_from_return_inner(inner)
    if schema is None:
        schema = _row_schema_from_bound_args(bound_args)
    if schema is None:
        return None

    dt = z3_datatype_for_row(schema)
    # Build fresh symbolic array + length keyed by a unique name per call.
    uid = next(_QUERY_RETURN_COUNTER)
    name_base = f"{node.name}_result_{uid}"
    array_expr = z3.Const(name_base, z3.ArraySort(z3.IntSort(), dt))
    length_expr = z3.Int(f"{name_base}_len")
    quantifiers = _active_quantifiers(ctx)
    if quantifiers:
        indices = [q.bound_var for q in quantifiers]
        sorts = [index.sort() for index in indices]
        array_expr = z3.Function(f'{name_base}_by_iteration', *sorts, array_expr.sort())(*indices)
        length_expr = z3.Function(f'{name_base}_len_by_iteration', *sorts, z3.IntSort())(*indices)
    ctx.given.add(types.bool(length_expr >= 0))
    value = QueryResultSort(
        row_schema=schema,
        array_expr=array_expr,
        length_expr=length_expr,
        source=(node.name, bound_args),
    )
    add_value_domain(value, ctx=ctx)
    return value


def _row_schema_from_return_inner(inner: astroid.NodeNG) -> "type | None":
    from ._row import row_schema_from_class_def

    if not isinstance(inner, (astroid.Name, astroid.Attribute)):
        return None
    resolved = next(inner.infer(), None)
    if not isinstance(resolved, astroid.ClassDef):
        return None
    return row_schema_from_class_def(resolved)


def _row_schema_from_bound_args(bound_args: dict[str, ProxySort]) -> "type | None":
    from ._row import RowClassSort

    schema_arg = bound_args.get("schema")
    if isinstance(schema_arg, RowClassSort):
        return schema_arg.schema

    schemas = [
        value.schema for value in bound_args.values() if isinstance(value, RowClassSort)
    ]
    if len(schemas) == 1:
        return schemas[0]
    return None

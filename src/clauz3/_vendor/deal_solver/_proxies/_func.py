from __future__ import annotations

import typing

import astroid
import z3

from .._exceptions import UnsupportedError
from ._funcs import and_expr
from ._proxy import ProxySort
from ._registry import types


if typing.TYPE_CHECKING:
    from .._context import Context


F = typing.Callable[..., ProxySort]


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
        return self.impl(*args, ctx=ctx, **kwargs)

    @staticmethod
    def _call_function(
        node: astroid.FunctionDef,
        ctx: Context,
        call_args: tuple[ProxySort, ...],
        call_kwargs: dict[str, ProxySort],
    ) -> ProxySort:
        from .._eval_contracts import eval_contracts
        from .._eval_stmt import eval_stmt
        from .._context import FactInfo

        # put arguments into the scope
        func_ctx = ctx.make_empty(
            get_contracts=ctx.get_contracts,
            get_guarantees=ctx.get_guarantees,
            trace=ctx.trace,
        )
        bound_args = _bind_args(node=node, call_args=call_args, call_kwargs=call_kwargs)
        for name, value in bound_args.items():
            func_ctx.scope.set(name=name, value=value)

        markers = _trusted_markers(node=node, ctx=ctx)
        if markers:
            # clauz3 extension: @deal.has(...) marks a trusted boundary.
            # We require the trusted function preconditions, record the call
            # as a symbolic fact for guarantee solvers, and deliberately skip
            # the side-effecting function body. If the return annotation is
            # list[<Row>], materialize a QueryResultSort instead of bool.True.
            contracts = eval_contracts(func=node, ctx=func_ctx)
            active_quantifiers = _active_quantifiers(ctx)
            for pre in contracts.pre:
                wrapped = (
                    _wrap_pre_in_quantifiers(pre, active_quantifiers, ctx=ctx)
                    if active_quantifiers
                    else pre
                )
                ctx.expected.add(wrapped)
            ctx.facts.add(
                FactInfo(
                    name=node.name,
                    markers=markers,
                    args=bound_args,
                    cond=ctx.interrupted.m_not(ctx=ctx),
                    quantifiers=active_quantifiers,
                )
            )
            materialized = _materialize_trusted_return(
                node=node,
                bound_args=bound_args,
                ctx=ctx,
            )
            if materialized is not None:
                # Trusted postconditions are constraints on materialized value.
                # Inject the materialized return into func_ctx so _eval_post
                # can bind `result` to it when evaluating the lambda.
                from .._context import ReturnInfo

                true_cond = types.bool.val(True, ctx=ctx)
                func_ctx.returns.add(ReturnInfo(value=materialized, cond=true_cond))
                post_contracts = eval_contracts(func=node, ctx=func_ctx)
                ctx.given.add(and_expr(*post_contracts.post, ctx=ctx))
                return materialized
            return types.bool.val(True, ctx=ctx)

        # call the function
        eval_stmt(node=node, ctx=func_ctx)
        result = func_ctx.return_value
        for fact in func_ctx.facts.layer:
            ctx.facts.add(fact)

        # we ask pre-conditions to be true
        # and promise post-condition to be true
        contracts = eval_contracts(func=node, ctx=func_ctx)
        ctx.expected.add(and_expr(*contracts.pre, ctx=ctx))
        ctx.given.add(and_expr(*contracts.post, ctx=ctx))

        if result is None:
            return types.bool.val(True, ctx=ctx)
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


def _wrap_pre_in_quantifiers(pre_proxy, quantifiers, *, ctx: "Context"):
    """Wrap a precondition in ForAll over the active quantifier scope.

    Each quantifier in *quantifiers* contributes a bound variable and a
    bounds expression (lower <= i < upper).  The wrapped formula asserts
    that for every valid loop index the precondition holds.
    """
    if not quantifiers:
        return pre_proxy
    bound_vars = [q.bound_var for q in quantifiers]
    bounds = z3.And(*[q.bounds_expr() for q in quantifiers])
    pre_z3 = pre_proxy.expr if hasattr(pre_proxy, "expr") else pre_proxy
    wrapped = z3.ForAll(bound_vars, z3.Implies(bounds, pre_z3))
    return types.bool(expr=wrapped)


def _bind_args(
    node: astroid.FunctionDef,
    call_args: tuple[ProxySort, ...],
    call_kwargs: dict[str, ProxySort],
) -> dict[str, ProxySort]:
    bound: dict[str, ProxySort] = {}
    for arg, value in zip(node.args.args or [], call_args):
        bound[arg.name] = value
    bound.update(call_kwargs)
    return bound


def _trusted_markers(node: astroid.FunctionDef, ctx: Context) -> tuple[str, ...]:
    markers: list[str] = []
    for contract in ctx.get_contracts(node):
        if contract.name != "has":
            continue
        for arg in contract.args:
            if isinstance(arg, astroid.Const) and isinstance(arg.value, str):
                markers.append(arg.value)
    return tuple(markers)


def _materialize_trusted_return(
    *,
    node: astroid.FunctionDef,
    bound_args: dict[str, ProxySort],
    ctx: Context,
) -> "ProxySort | None":
    """If the trusted function returns list[Row], return a QueryResultSort.

    Otherwise return None and let the caller fall back to bool.val(True).
    """
    import z3

    from ._row import QueryResultSort, z3_datatype_for_row

    returns = node.returns
    if returns is None:
        return None
    # Pattern: list[SomeRowSubclass]
    if not isinstance(returns, astroid.Subscript):
        return None
    if not isinstance(returns.value, astroid.Name) or returns.value.name != "list":
        return None
    inner = returns.slice
    if not isinstance(inner, astroid.Name):
        return None

    resolved = next(inner.infer(), None)
    if not isinstance(resolved, astroid.ClassDef):
        return None

    if not _is_row_subclass(resolved):
        return None

    schema = _schema_from_class_def(resolved)
    if schema is None:
        return None

    dt = z3_datatype_for_row(schema)
    # Build fresh symbolic array + length keyed by a unique name per call.
    name_base = f"{node.name}_result_{id(ctx)}"
    array_expr = z3.Const(name_base, z3.ArraySort(z3.IntSort(), dt))
    length_expr = z3.Int(f"{name_base}_len")
    ctx.given.add(types.bool(length_expr >= 0))
    return QueryResultSort(
        row_schema=schema,
        array_expr=array_expr,
        length_expr=length_expr,
        source=(node.name, bound_args),
    )


def _is_row_subclass(class_def: astroid.ClassDef) -> bool:
    """Return True if class_def syntactically extends clauz3.Row or Row."""
    for base in class_def.bases:
        base_str = base.as_string()
        if base_str in ("clauz3.Row", "Row"):
            return True
        # Also check via real class resolution if the module is importable
        real_cls = _resolve_real_class(class_def)
        if real_cls is not None:
            from clauz3.row import Row

            if issubclass(real_cls, Row):
                return True
    return False


def _resolve_real_class(class_def: astroid.ClassDef) -> "type | None":
    """Try to resolve an astroid ClassDef to its actual Python class.

    Returns None if the module is not importable (e.g., inline source).
    """
    import importlib
    import sys

    module_name = class_def.root().name
    if not module_name:
        return None
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            return None
        module = importlib.import_module(module_name)
    return getattr(module, class_def.name, None)


_ANNOTATION_NAME_TO_TYPE: dict[str, type] = {"str": str, "int": int, "bool": bool}


def _schema_from_class_def(class_def: astroid.ClassDef) -> "type | None":
    """Build a minimal schema object from an astroid ClassDef.

    First tries to resolve the real Python class (for imported modules).
    Falls back to building a synthetic class with __annotations__ from the AST.
    """
    real = _resolve_real_class(class_def)
    if real is not None:
        return real

    # Build a synthetic class with __annotations__ populated from the AST
    annotations: dict[str, type] = {}
    for node in class_def.body:
        if not isinstance(node, astroid.AnnAssign):
            continue
        if not isinstance(node.target, astroid.AssignName):
            continue
        if not isinstance(node.annotation, astroid.Name):
            continue
        field_type = _ANNOTATION_NAME_TO_TYPE.get(node.annotation.name)
        if field_type is None:
            return None  # unsupported field type; bail out
        annotations[node.target.name] = field_type

    if not annotations:
        return None

    # Create a synthetic class that mirrors the Row's duck-type contract
    synthetic = type(class_def.name, (), {"__annotations__": annotations})
    return synthetic

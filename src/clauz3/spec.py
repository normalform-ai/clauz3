"""Declarative contract helpers over trusted effect facts."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from clauz3.row import ColumnRef

from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._funcs._registry import register
from clauz3._vendor.deal_solver._proxies import (
    BoolSort,
    ProxySort,
    UntypedListSort,
    and_expr,
    if_expr,
    or_expr,
    types,
)

F = TypeVar("F", bound=Callable[..., object])

# String methods allowed inside relation lambdas. These map onto the matching
# proxy methods (PrefixOf / SuffixOf), letting contracts express path-prefix
# policies such as "every write path is under this root".
_STR_METHODS = frozenset({"startswith", "endswith"})


def contract(func: F) -> F:
    """Register a domain contract helper.

    The helper itself builds a declarative ContractSpec. The registered wrapper
    is the bridge deal-solver calls when it sees ``module.helper(...)`` in a
    guarantee.
    """

    register(f"{func.__module__}.{func.__name__}")(_solve_with(func))
    return func


def effect(name: str) -> EffectRelation:
    """Create a relation over trusted calls.

    ``name`` can be a marker from ``@deal.has(name)`` or a trusted function
    name. Row fields are inferred from the trusted function's parameters.
    """

    return EffectRelation(name=name)


def no_guarantees() -> ContractSpec:
    """Return a contract that imposes no constraints."""
    return NoGuaranteesSpec()


def _solve_with(func: F) -> Callable[..., BoolSort]:
    def solve(*args: ProxySort, ctx: Any, **kwargs: ProxySort) -> BoolSort:
        unwrapped_args = [_unwrap_proxy_arg(a) for a in args]
        unwrapped_kwargs = {k: _unwrap_proxy_arg(v) for k, v in kwargs.items()}
        result = func(*unwrapped_args, **unwrapped_kwargs)
        if not isinstance(result, ContractSpec):
            raise UnsupportedError("contract helper must return ContractSpec")
        return result.solve(ctx=ctx)

    return solve


def _unwrap_proxy_arg(value: Any) -> Any:
    """Unwrap a ProxySort to its underlying Python value for contract calls.

    - RowClassSort → the wrapped Python class
    - StrSort with a string literal expr → the Python str
    - Other ProxySorts → passed through unchanged (contract will handle them)
    """
    import z3

    from clauz3._vendor.deal_solver._proxies._row import RowClassSort
    from clauz3._vendor.deal_solver._proxies._str import StrSort

    if isinstance(value, RowClassSort):
        return value.schema

    if isinstance(value, StrSort) and z3.is_string_value(value.expr):
        return value.expr.as_string()

    return value


class ContractSpec:
    def solve(self, *, ctx: Any) -> BoolSort:
        raise NotImplementedError


@dataclass(frozen=True)
class NoGuaranteesSpec(ContractSpec):
    def solve(self, *, ctx: Any) -> BoolSort:
        return types.bool.val(True, ctx=ctx)


@dataclass(frozen=True)
class EffectRelation:
    name: str

    def all(self, predicate: Callable[[Any], Any]) -> ContractSpec:
        return AllSpec(relation=self, predicate=LambdaSpec.from_callable(predicate))

    def where(self, predicate: Callable[[Any], Any]) -> FilteredRelation:
        return FilteredRelation(
            relation=self,
            predicate=LambdaSpec.from_callable(predicate),
        )

    def empty(self) -> ContractSpec:
        return EmptySpec(relation=self)

    def distinct(self, key: Callable[[Any], Any]) -> ContractSpec:
        return DistinctSpec(relation=self, key=LambdaSpec.from_callable(key))

    def sum(self, selector: Callable[[Any], Any]) -> SumSpec:
        return SumSpec(
            relation=self,
            selector=LambdaSpec.from_callable(selector),
            predicate=None,
        )

    def count(self) -> SumSpec:
        return SumSpec(relation=self, selector=None, predicate=None)

    def facts(self, ctx: Any) -> list[Any]:
        return [
            fact
            for fact in ctx.facts
            if self.name == fact.name or self.name in fact.markers
        ]


@dataclass(frozen=True)
class FilteredRelation:
    relation: EffectRelation
    predicate: LambdaSpec

    def all(self, predicate: Callable[[Any], Any]) -> ContractSpec:
        return AllSpec(
            relation=self.relation,
            predicate=LambdaSpec.from_callable(predicate),
            fact_filter=self.predicate,
        )

    def empty(self) -> ContractSpec:
        return EmptySpec(relation=self.relation, fact_filter=self.predicate)

    def distinct(self, key: Callable[[Any], Any]) -> ContractSpec:
        return DistinctSpec(
            relation=self.relation,
            key=LambdaSpec.from_callable(key),
            fact_filter=self.predicate,
        )

    def sum(self, selector: Callable[[Any], Any]) -> SumSpec:
        return SumSpec(
            relation=self.relation,
            selector=LambdaSpec.from_callable(selector),
            predicate=self.predicate,
        )

    def count(self) -> SumSpec:
        return SumSpec(relation=self.relation, selector=None, predicate=self.predicate)

    def shares_value(
        self,
        other: FilteredRelation,
        key: Callable[[Any], Any],
    ) -> ContractSpec:
        return SharedValueSpec(
            left_relation=self.relation,
            left_filter=self.predicate,
            right_relation=other.relation,
            right_filter=other.predicate,
            key=LambdaSpec.from_callable(key),
        )


@dataclass(frozen=True)
class AllSpec(ContractSpec):
    relation: EffectRelation
    predicate: LambdaSpec
    fact_filter: LambdaSpec | None = None

    def solve(self, *, ctx: Any) -> BoolSort:
        clauses = []
        for fact in self.relation.facts(ctx):
            cond = _fact_cond(fact=fact, predicate=self.fact_filter, ctx=ctx)
            body_pred = or_expr(
                cond.m_not(ctx=ctx),
                self.predicate.evaluate(row=fact.args, ctx=ctx).m_bool(ctx=ctx),
                ctx=ctx,
            )
            wrapped = _wrap_with_quantifiers(fact, body_pred, ctx=ctx)
            clauses.append(wrapped)
        return and_expr(*clauses, ctx=ctx)


@dataclass(frozen=True)
class EmptySpec(ContractSpec):
    relation: EffectRelation
    fact_filter: LambdaSpec | None = None

    def solve(self, *, ctx: Any) -> BoolSort:
        clauses = []
        for fact in self.relation.facts(ctx):
            cond = _fact_cond(fact=fact, predicate=self.fact_filter, ctx=ctx)
            body_pred = cond.m_not(ctx=ctx)
            wrapped = _wrap_with_quantifiers(fact, body_pred, ctx=ctx)
            clauses.append(wrapped)
        return and_expr(*clauses, ctx=ctx)


@dataclass(frozen=True)
class DistinctSpec(ContractSpec):
    relation: EffectRelation
    key: LambdaSpec
    fact_filter: LambdaSpec | None = None

    def solve(self, *, ctx: Any) -> BoolSort:
        clauses = []
        facts = self.relation.facts(ctx)
        for index, left in enumerate(facts):
            left_cond = _fact_cond(fact=left, predicate=self.fact_filter, ctx=ctx)
            # Cross-fact pairs (existing behavior, now quantifier-aware)
            for right in facts[index + 1 :]:
                right_cond = _fact_cond(
                    fact=right,
                    predicate=self.fact_filter,
                    ctx=ctx,
                )
                clauses.append(
                    _distinct_pair_clause(
                        left=left,
                        right=right,
                        left_cond=left_cond,
                        right_cond=right_cond,
                        key=self.key,
                        ctx=ctx,
                    )
                )
            # Same-fact case: only meaningful when fact has quantifiers
            if getattr(left, "quantifiers", ()):
                clauses.append(
                    _distinct_same_fact_clause(
                        fact=left,
                        cond=left_cond,
                        key=self.key,
                        ctx=ctx,
                    )
                )
        return and_expr(*clauses, ctx=ctx)


def _distinct_pair_clause(
    *,
    left: Any,
    right: Any,
    left_cond: Any,
    right_cond: Any,
    key: LambdaSpec,
    ctx: Any,
) -> BoolSort:
    """Cross-fact: ∀ qvars_left, qvars_right: bounds ∧ conds → key(left) ≠ key(right)."""  # noqa: E501
    import z3

    left_key = key.evaluate(row=left.args, ctx=ctx)
    right_key = key.evaluate(row=right.args, ctx=ctx)
    body = or_expr(
        left_cond.m_not(ctx=ctx),
        right_cond.m_not(ctx=ctx),
        left_key.m_ne(right_key, ctx=ctx),
        ctx=ctx,
    )
    quantifiers = list(getattr(left, "quantifiers", ())) + list(
        getattr(right, "quantifiers", ())
    )
    if not quantifiers:
        return body
    bound_vars = [q.bound_var for q in quantifiers]
    bounds_parts = [q.bounds_expr() for q in quantifiers]
    bounds = z3.And(*bounds_parts) if len(bounds_parts) > 1 else bounds_parts[0]
    body_z3 = body.expr
    wrapped = z3.ForAll(bound_vars, z3.Implies(bounds, body_z3))
    return types.bool(expr=wrapped)


def _distinct_same_fact_clause(
    *, fact: Any, cond: Any, key: LambdaSpec, ctx: Any
) -> BoolSort:
    """Same-fact: ∀ i≠j, bounds(i) ∧ bounds(j) ∧ cond(i) ∧ cond(j) → key(i) ≠ key(j).

    Substitutes the bound vars with fresh copies per side of the comparison.
    """
    import z3

    fresh_left = [z3.Int(f"{q.bound_var}_lhs_{id(fact)}") for q in fact.quantifiers]
    fresh_right = [z3.Int(f"{q.bound_var}_rhs_{id(fact)}") for q in fact.quantifiers]

    subs_left = list(
        zip([q.bound_var for q in fact.quantifiers], fresh_left, strict=True)
    )
    subs_right = list(
        zip([q.bound_var for q in fact.quantifiers], fresh_right, strict=True)
    )

    args_left = {k: _substituted_proxy(v, subs_left) for k, v in fact.args.items()}
    args_right = {k: _substituted_proxy(v, subs_right) for k, v in fact.args.items()}
    cond_left = _substituted_proxy(cond, subs_left)
    cond_right = _substituted_proxy(cond, subs_right)

    key_left = key.evaluate(row=args_left, ctx=ctx)
    key_right = key.evaluate(row=args_right, ctx=ctx)

    different_iteration = z3.Or(
        *[lv != rv for lv, rv in zip(fresh_left, fresh_right, strict=True)]
    )
    bounds = z3.And(
        *[
            z3.And(q.lower <= lv, lv < q.upper)
            for q, lv in zip(fact.quantifiers, fresh_left, strict=True)
        ],
        *[
            z3.And(q.lower <= rv, rv < q.upper)
            for q, rv in zip(fact.quantifiers, fresh_right, strict=True)
        ],
    )
    cond_left_z3 = cond_left.expr if hasattr(cond_left, "expr") else cond_left
    cond_right_z3 = cond_right.expr if hasattr(cond_right, "expr") else cond_right
    key_diff = key_left.m_ne(key_right, ctx=ctx)
    key_diff_z3 = key_diff.expr if hasattr(key_diff, "expr") else key_diff

    body = z3.Implies(
        z3.And(different_iteration, bounds, cond_left_z3, cond_right_z3),
        key_diff_z3,
    )
    wrapped = z3.ForAll(fresh_left + fresh_right, body)
    return types.bool(expr=wrapped)


def _substituted_proxy(value: Any, subs: list[tuple[Any, Any]]) -> Any:
    """Apply Z3 substitute() to the underlying expression, rewrapping in the same type.

    Returns non-proxy values (Python literals) unchanged. Special-cases proxy
    types that carry metadata beyond the canonical ``expr`` attribute so the
    metadata is preserved through substitution. Falling through to the default
    ``proxy_type(expr=new_expr)`` path for unknown metadata-carrying proxies
    would silently drop their schema/subtypes, so we raise a pointing
    UnsupportedError instead.
    """
    import z3

    from clauz3._vendor.deal_solver._proxies._row import RowSort

    if not hasattr(value, "expr"):
        return value
    new_expr = z3.substitute(value.expr, subs)

    if isinstance(value, RowSort):
        return RowSort(schema=value.schema, expr=new_expr)

    proxy_type = type(value)
    try:
        return proxy_type(expr=new_expr)
    except TypeError as exc:
        raise UnsupportedError(
            f"distinct over a quantified fact with arg type "
            f"{proxy_type.__name__!r} is not supported in v1 — "
            f"the proxy carries metadata that _substituted_proxy "
            f"cannot reconstruct ({exc}). See issue #13."
        ) from exc


@dataclass(frozen=True)
class SumSpec:
    relation: EffectRelation
    selector: LambdaSpec | None
    predicate: LambdaSpec | None

    def __le__(self, other: Any) -> ContractSpec:
        return ComparisonSpec(left=self, op="<=", right=other)

    def __lt__(self, other: Any) -> ContractSpec:
        return ComparisonSpec(left=self, op="<", right=other)

    def __ge__(self, other: Any) -> ContractSpec:
        return ComparisonSpec(left=self, op=">=", right=other)

    def __gt__(self, other: Any) -> ContractSpec:
        return ComparisonSpec(left=self, op=">", right=other)

    def value(self, *, ctx: Any) -> ProxySort:
        import z3

        total: ProxySort = types.int.val(0, ctx=ctx)
        zero = types.int.val(0, ctx=ctx)
        for fact in self.relation.facts(ctx):
            cond = _fact_cond(fact=fact, predicate=self.predicate, ctx=ctx)

            if self.selector is None:
                base: ProxySort = types.int.val(1, ctx=ctx)
            else:
                base = self.selector.evaluate(row=fact.args, ctx=ctx)

            quantifiers = getattr(fact, "quantifiers", ())
            if not quantifiers:
                contrib = if_expr(cond, base, zero, ctx=ctx)
            else:
                if self.selector is not None:
                    bound_vars = [q.bound_var for q in quantifiers]
                    if _expr_uses_any_bound_var(base, bound_vars):
                        raise UnsupportedError(
                            "sum(selector) where selector depends on the loop "
                            "variable is not supported in v1; see "
                            "docs/todos/quantified-aggregates.md"
                        )
                product_z3: z3.ArithRef = z3.IntVal(1)
                for q in quantifiers:
                    product_z3 = product_z3 * q.upper
                product: ProxySort = types.int(expr=product_z3)
                multiplied = base.m_mul(product, ctx=ctx)
                contrib = if_expr(cond, multiplied, zero, ctx=ctx)

            total = total.m_add(contrib, ctx=ctx)
        return total


@dataclass(frozen=True)
class ComparisonSpec(ContractSpec):
    left: SumSpec
    op: str
    right: Any

    def solve(self, *, ctx: Any) -> BoolSort:
        left = self.left.value(ctx=ctx)
        right = _as_proxy(self.right, ctx=ctx)
        if self.op == "<=":
            return left.m_le(right, ctx=ctx)
        if self.op == "<":
            return left.m_lt(right, ctx=ctx)
        if self.op == ">=":
            return left.m_ge(right, ctx=ctx)
        if self.op == ">":
            return left.m_gt(right, ctx=ctx)
        raise RuntimeError("unreachable")


@dataclass(frozen=True)
class SharedValueSpec(ContractSpec):
    left_relation: EffectRelation
    left_filter: LambdaSpec | None
    right_relation: EffectRelation
    right_filter: LambdaSpec | None
    key: LambdaSpec

    def solve(self, *, ctx: Any) -> BoolSort:
        # v1: shares_value across quantified relations not supported
        all_facts = self.left_relation.facts(ctx) + self.right_relation.facts(ctx)
        for fact in all_facts:
            if getattr(fact, "quantifiers", ()):
                raise UnsupportedError(
                    "shares_value across quantified relations is not "
                    "supported in v1; see docs/todos/quantified-shares-value.md"
                )
        clauses = []
        for left in self.left_relation.facts(ctx):
            left_cond = _fact_cond(fact=left, predicate=self.left_filter, ctx=ctx)
            left_key = self.key.evaluate(row=left.args, ctx=ctx)
            for right in self.right_relation.facts(ctx):
                right_cond = _fact_cond(
                    fact=right,
                    predicate=self.right_filter,
                    ctx=ctx,
                )
                right_key = self.key.evaluate(row=right.args, ctx=ctx)
                clauses.append(
                    and_expr(
                        left_cond,
                        right_cond,
                        left_key.m_eq(right_key, ctx=ctx),
                        ctx=ctx,
                    )
                )
        return or_expr(*clauses, ctx=ctx)


def _fact_cond(*, fact: Any, predicate: LambdaSpec | None, ctx: Any) -> BoolSort:
    cond = cast(BoolSort, fact.cond)
    if predicate is None:
        return cond
    return and_expr(
        cond,
        predicate.evaluate(row=fact.args, ctx=ctx).m_bool(ctx=ctx),
        ctx=ctx,
    )


def _wrap_with_quantifiers(
    fact: Any,
    body: BoolSort,
    *,
    ctx: Any,
) -> BoolSort:
    """Wrap a relation body in ForAll over fact.quantifiers.

    Short-circuits when fact has no quantifiers (existing behavior preserved).
    """
    import z3

    quantifiers = getattr(fact, "quantifiers", ())
    if not quantifiers:
        return body
    body_z3 = body.expr
    bound_vars = [q.bound_var for q in quantifiers]
    bounds_parts = [q.bounds_expr() for q in quantifiers]
    bounds = z3.And(*bounds_parts) if len(bounds_parts) > 1 else bounds_parts[0]
    wrapped = z3.ForAll(bound_vars, z3.Implies(bounds, body_z3))
    return types.bool(wrapped)


@dataclass(frozen=True)
class LambdaSpec:
    arg_name: str
    body: ast.expr
    env: dict[str, Any]

    @classmethod
    def from_callable(cls, func: Callable[[Any], Any]) -> LambdaSpec:
        # Parse the whole defining file rather than inspect.getsourcelines(func):
        # for a lambda passed as a call argument on its own continuation line,
        # getsourcelines returns only the lambda's first physical line, silently
        # truncating multi-line bodies (e.g. a chained ``and``). Locating the
        # node in the full module by its first line number keeps the body whole.
        all_lines, _ = inspect.findsource(func)
        module = ast.parse("".join(all_lines))
        firstlineno = func.__code__.co_firstlineno
        lambdas = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Lambda) and node.lineno == firstlineno
        ]
        if len(lambdas) > 1 and func.__code__.co_argcount:
            first_arg = func.__code__.co_varnames[0]
            narrowed = [
                node
                for node in lambdas
                if node.args.args and node.args.args[0].arg == first_arg
            ]
            if narrowed:
                lambdas = narrowed
        if not lambdas:
            raise UnsupportedError("could not find lambda source")
        lambda_node = lambdas[0]
        if len(lambda_node.args.args) != 1:
            raise UnsupportedError("effect lambdas must have exactly one argument")
        closure = inspect.getclosurevars(func)
        env = {**closure.globals, **closure.nonlocals}
        return cls(
            arg_name=lambda_node.args.args[0].arg,
            body=lambda_node.body,
            env=env,
        )

    def evaluate(self, *, row: dict[str, ProxySort], ctx: Any) -> ProxySort:
        return _ExpressionCompiler(
            arg_name=self.arg_name,
            row=row,
            env=self.env,
            ctx=ctx,
        ).eval(self.body)


@dataclass(frozen=True)
class _ExpressionCompiler:
    arg_name: str
    row: dict[str, ProxySort]
    env: dict[str, Any]
    ctx: Any

    def eval(self, node: ast.AST) -> ProxySort:
        if isinstance(node, ast.Constant):
            return cast(ProxySort, _as_proxy(node.value, ctx=self.ctx))
        if isinstance(node, ast.List):
            return _list_value([self.eval(item) for item in node.elts], ctx=self.ctx)
        if isinstance(node, ast.Name):
            if node.id == self.arg_name:
                raise UnsupportedError("bare effect row is not supported")
            if node.id in self.env:
                return cast(ProxySort, _as_proxy(self.env[node.id], ctx=self.ctx))
            raise UnsupportedError("unknown name in effect lambda", node.id)
        if isinstance(node, ast.Attribute):
            return self._eval_attribute(node)
        if isinstance(node, ast.Compare):
            return self._eval_compare(node)
        if isinstance(node, ast.BoolOp):
            return self._eval_bool_op(node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self.eval(node.operand).m_not(ctx=self.ctx)
        if isinstance(node, ast.BinOp):
            return self._eval_bin_op(node)
        if isinstance(node, ast.Call):
            return self._eval_call(node)
        raise UnsupportedError(
            "unsupported expression in effect lambda",
            type(node).__name__,
        )

    def _eval_attribute(self, node: ast.Attribute) -> ProxySort:
        if isinstance(node.value, ast.Name) and node.value.id == self.arg_name:
            if node.attr not in self.row:
                raise UnsupportedError("unknown effect field", node.attr)
            return self.row[node.attr]
        raise UnsupportedError("only effect row attributes are supported")

    def _eval_compare(self, node: ast.Compare) -> ProxySort:
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise UnsupportedError("chained comparisons are not supported")
        left = self.eval(node.left)
        right = self.eval(node.comparators[0])
        op = node.ops[0]

        # Column-ref equality dispatch
        left_is_col = getattr(left, "is_column_ref", False)
        right_is_col = getattr(right, "is_column_ref", False)
        if isinstance(op, (ast.Eq, ast.NotEq)) and (left_is_col or right_is_col):
            col_proxy = cast(Any, left if left_is_col else right)
            other = right if left_is_col else left
            result = _compare_column_ref(other, col_proxy.column, ctx=self.ctx)
            if isinstance(op, ast.NotEq):
                return result.m_not(ctx=self.ctx)
            return result

        if isinstance(op, ast.Eq):
            return left.m_eq(right, ctx=self.ctx)
        if isinstance(op, ast.NotEq):
            return left.m_ne(right, ctx=self.ctx)
        if isinstance(op, ast.Lt):
            return left.m_lt(right, ctx=self.ctx)
        if isinstance(op, ast.LtE):
            return left.m_le(right, ctx=self.ctx)
        if isinstance(op, ast.Gt):
            return left.m_gt(right, ctx=self.ctx)
        if isinstance(op, ast.GtE):
            return left.m_ge(right, ctx=self.ctx)
        if isinstance(op, ast.In):
            return right.m_contains(left, ctx=self.ctx)
        if isinstance(op, ast.NotIn):
            return right.m_contains(left, ctx=self.ctx).m_not(ctx=self.ctx)
        raise UnsupportedError("unsupported comparison in effect lambda")

    def _eval_bool_op(self, node: ast.BoolOp) -> ProxySort:
        values = [self.eval(value).m_bool(ctx=self.ctx) for value in node.values]
        if isinstance(node.op, ast.And):
            return and_expr(*values, ctx=self.ctx)
        if isinstance(node.op, ast.Or):
            return or_expr(*values, ctx=self.ctx)
        raise RuntimeError("unreachable")

    def _eval_bin_op(self, node: ast.BinOp) -> ProxySort:
        left = self.eval(node.left)
        right = self.eval(node.right)
        if isinstance(node.op, ast.Add):
            return left.m_add(right, ctx=self.ctx)
        if isinstance(node.op, ast.Sub):
            return left.m_sub(right, ctx=self.ctx)
        raise UnsupportedError("unsupported binary operation in effect lambda")

    def _eval_call(self, node: ast.Call) -> ProxySort:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "len"
            and len(node.args) == 1
            and not node.keywords
        ):
            return self.eval(node.args[0]).m_len(ctx=self.ctx)
        if isinstance(node.func, ast.Attribute) and node.func.attr in _STR_METHODS:
            return self._eval_str_method(node, node.func)
        raise UnsupportedError("unsupported call in effect lambda")

    def _eval_str_method(self, node: ast.Call, func: ast.Attribute) -> ProxySort:
        if node.keywords or len(node.args) != 1:
            raise UnsupportedError(
                "string method in effect lambda takes one positional argument",
                func.attr,
            )
        obj = self.eval(func.value)
        arg = self.eval(node.args[0])
        method = obj.methods.get(func.attr)
        if method is None:
            raise UnsupportedError("unsupported method in effect lambda", func.attr)
        return method.with_obj(obj).m_call(arg, ctx=self.ctx)


@dataclass(frozen=True)
class _ColumnRefProxy:
    """Sentinel wrapper for ColumnRef in lambda compilation."""

    column: ColumnRef
    ctx: Any

    @property
    def is_column_ref(self) -> bool:
        return True


def _as_proxy(value: Any, *, ctx: Any) -> Any:
    from clauz3.row import ColumnRef

    if isinstance(value, ProxySort):
        return value
    if isinstance(value, ColumnRef):
        return _ColumnRefProxy(column=value, ctx=ctx)
    if isinstance(value, bool):
        return types.bool.val(value, ctx=ctx)
    if isinstance(value, int):
        return types.int.val(value, ctx=ctx)
    if isinstance(value, str):
        return types.str.val(value, ctx=ctx)
    if isinstance(value, list):
        return _list_value([_as_proxy(item, ctx=ctx) for item in value], ctx=ctx)
    raise UnsupportedError("unsupported contract literal", type(value).__name__)


def _compare_column_ref(arg_proxy: Any, column_ref: ColumnRef, *, ctx: Any) -> BoolSort:
    """Structural match: does arg_proxy's symbolic value have the shape
    <column_ref.field selector>(array_select(<query of column_ref.schema>, ?))?

    Returns BoolSort.val(True) on match, BoolSort.val(False) otherwise.
    """
    import z3

    from clauz3._vendor.deal_solver._proxies._row import z3_datatype_for_row

    expr = arg_proxy.expr if hasattr(arg_proxy, "expr") else arg_proxy
    if not isinstance(expr, z3.ExprRef):
        return types.bool.val(False, ctx=ctx)

    # 1) Top-level must be a single-argument function application matching
    #    the field accessor name.
    if expr.num_args() != 1:
        return types.bool.val(False, ctx=ctx)
    if expr.decl().name() != column_ref.field:
        return types.bool.val(False, ctx=ctx)

    # 2) The argument should be a Select(array, index).
    inner = expr.arg(0)
    if inner.decl().kind() != z3.Z3_OP_SELECT:
        return types.bool.val(False, ctx=ctx)

    # 3) The array's range sort should match the schema's Z3 datatype.
    array_expr = inner.arg(0)
    if not z3.is_array(array_expr):
        return types.bool.val(False, ctx=ctx)
    target_dt = z3_datatype_for_row(column_ref.schema)
    if array_expr.sort().range() != target_dt:
        return types.bool.val(False, ctx=ctx)

    return types.bool.val(True, ctx=ctx)


def _list_value(values: list[ProxySort], *, ctx: Any) -> ProxySort:
    if not values:
        return UntypedListSort()
    return types.list.val(values, ctx=ctx)


def _expr_uses_any_bound_var(proxy: Any, bound_vars: list[Any]) -> bool:
    """Return True if proxy's Z3 expression references any of the given bound vars.

    >>> import z3
    >>> i = z3.Int('i')
    >>> arr = z3.Array('arr', z3.IntSort(), z3.IntSort())
    >>> val = z3.Select(arr, i)
    >>> _expr_uses_any_bound_var(val, [i])
    True
    >>> _expr_uses_any_bound_var(z3.IntVal(42), [i])
    False
    """
    import z3

    expr = proxy.expr if hasattr(proxy, "expr") else proxy
    if not isinstance(expr, z3.ExprRef):
        return False

    def _walk(e: z3.ExprRef) -> bool:
        for bv in bound_vars:
            if e.eq(bv):
                return True
        return any(_walk(c) for c in e.children())

    return _walk(expr)

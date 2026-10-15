"""Evaluate contracts against concrete interpreter traces without the prover.

The relation operations use ordinary Python collections, arithmetic and predicate
execution. No ``ContractSpec.solve`` or symbolic predicate compiler is called.
``ColumnRef`` comparisons check membership in observed query column values: this
is the observable, extensional conclusion, not a test of symbolic provenance.
"""

from __future__ import annotations

import ast
import copy
import inspect
import operator
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import z3

from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import ProxySort
from clauz3._vendor.deal_solver._strings import is_string_expr, string_value
from clauz3.fluent import Fluent, FluentAllSpec, FluentCompareSpec, _ParamRef
from clauz3.row import ColumnRef, Row
from clauz3.spec import (
    AllSpec,
    ComparisonSpec,
    ContractSpec,
    DistinctSpec,
    EffectRelation,
    EmptySpec,
    LambdaSpec,
    NoGuaranteesSpec,
    SharedValueSpec,
    SumSpec,
)

EffectCall = tuple[str, tuple[Any, ...], dict[str, Any]]


class ContractEvaluationError(ValueError):
    """A trace or contract cannot be checked by the concrete oracle."""


def check_contract(
    spec: ContractSpec,
    effects: Sequence[EffectCall],
    signatures: Mapping[str, inspect.Signature],
    *,
    row_sources: Sequence[Row] | None = None,
) -> bool:
    """Return whether the actual trusted calls satisfy ``spec``.

    ``signatures`` is keyed by qualified trusted-function name (or by its short
    name when unambiguous). Positional arguments, keywords and defaults are
    bound before evaluating a relation. ``row_sources`` contains the rows
    actually returned by trusted calls, including repeated calls; ``None``
    means this metadata was not collected and refuses column comparisons.

    Pass specs rebuilt with the source guarantee's Python literal arguments
    where possible. Literal solver proxies are accepted for compatibility, but
    converting a proxy cannot repair a literal already mistranslated upstream.
    Unknown spec kinds and unsupported predicates always raise, even for an
    empty trace. In particular, a universal fluent predicate over an infinite
    key domain may inspect its value, but cannot inspect its key.
    """
    try:
        oracle = _Oracle(signatures, row_sources)
        facts = [_bind_effect(call, signatures) for call in effects]
        return oracle.check(spec, facts)
    except ContractEvaluationError:
        raise
    except (ArithmeticError, AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ContractEvaluationError(
            f"concrete contract evaluation failed: {type(exc).__name__}: {exc}"
        ) from exc


@dataclass(frozen=True)
class _Fact:
    name: str
    args: dict[str, Any]


class _EffectRow:
    """Expose only bound parameters, including names like ``__class__``."""

    def __init__(self, values: dict[str, Any]) -> None:
        object.__setattr__(self, "_values", values)

    def __getattribute__(self, name: str) -> Any:
        values = object.__getattribute__(self, "_values")
        try:
            return values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _bind_effect(
    call: EffectCall, signatures: Mapping[str, inspect.Signature]
) -> _Fact:
    qualified, args, kwargs = call
    name = qualified.rsplit(".", 1)[-1]
    signature = signatures.get(qualified, signatures.get(name))
    if signature is None:
        raise ContractEvaluationError(f"missing trusted signature for {qualified!r}")
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    return _Fact(name, dict(bound.arguments))


@dataclass
class _Oracle:
    signatures: Mapping[str, inspect.Signature]
    row_sources: Sequence[Row] | None

    def check(self, spec: ContractSpec, facts: list[_Fact]) -> bool:
        if type(spec) is NoGuaranteesSpec:
            return True
        if type(spec) is AllSpec:
            predicate = self.predicate(
                spec.predicate, fields=self.relation_fields(spec.relation)
            )
            selected = self.select(spec.relation, spec.fact_filter, facts)
            return all(bool(predicate(fact.args)) for fact in selected)
        if type(spec) is EmptySpec:
            return not self.select(spec.relation, spec.fact_filter, facts)
        if type(spec) is DistinctSpec:
            key = self.predicate(spec.key, fields=self.relation_fields(spec.relation))
            selected = self.select(spec.relation, spec.fact_filter, facts)
            # Equality, rather than a set, also handles unhashable list keys.
            keys = [key(fact.args) for fact in selected]
            return all(
                left != right for i, left in enumerate(keys) for right in keys[i + 1 :]
            )
        if type(spec) is ComparisonSpec:
            compare = _COMPARISONS.get(spec.op)
            if compare is None:
                raise ContractEvaluationError(
                    f"unsupported aggregate comparison {spec.op!r}"
                )
            return bool(compare(self.total(spec.left, facts), _literal(spec.right)))
        if type(spec) is SharedValueSpec:
            fields = self.relation_fields(spec.left_relation) & self.relation_fields(
                spec.right_relation
            )
            key = self.predicate(spec.key, fields=fields)
            left = self.select(spec.left_relation, spec.left_filter, facts)
            right = self.select(spec.right_relation, spec.right_filter, facts)
            return any(key(a.args) == key(b.args) for a in left for b in right)
        if type(spec) is FluentCompareSpec:
            compare = _EQUALITY.get(spec.op)
            if compare is None:
                raise ContractEvaluationError(
                    f"unsupported fluent comparison {spec.op!r}"
                )
            state = self.fluent_state(spec.fluent, facts)
            value = state.get(_literal(spec.key), _literal(spec.fluent.initial))
            return bool(compare(value, _literal(spec.right)))
        if type(spec) is FluentAllSpec:
            predicate = self.predicate(
                spec.predicate, fields=frozenset({"key", "value"})
            )
            state = self.fluent_state(spec.fluent, facts)
            initial = _literal(spec.fluent.initial)
            if spec.fluent.key_type is bool:
                return all(
                    bool(predicate({"key": key, "value": state.get(key, initial)}))
                    for key in (False, True)
                )
            if "key" in spec.predicate.fields():
                raise ContractEvaluationError(
                    "universal fluent predicates inspecting an infinite key domain "
                    "are not supported"
                )
            # An infinite domain always has untouched keys, even if every key
            # appearing in the trace was updated. Check the initial value too.
            values = [initial, *state.values()]
            return all(bool(predicate({"value": value})) for value in values)
        raise ContractEvaluationError(
            f"unsupported contract spec {type(spec).__name__}"
        )

    def select(
        self,
        relation: EffectRelation,
        predicate: LambdaSpec | None,
        facts: list[_Fact],
    ) -> list[_Fact]:
        fields = self.relation_fields(relation)
        test = (
            self.predicate(predicate, fields=fields) if predicate is not None else None
        )
        return [
            fact
            for fact in facts
            if (not relation.names or fact.name in relation.names)
            and (test is None or bool(test(fact.args)))
        ]

    def total(self, spec: SumSpec, facts: list[_Fact]) -> Any:
        if type(spec) is not SumSpec:
            raise ContractEvaluationError(
                f"unsupported aggregate {type(spec).__name__}"
            )
        selector = (
            self.predicate(spec.selector, fields=self.relation_fields(spec.relation))
            if spec.selector is not None
            else None
        )
        selected = self.select(spec.relation, spec.predicate, facts)
        return sum(1 if selector is None else selector(fact.args) for fact in selected)

    def relation_fields(self, relation: EffectRelation) -> frozenset[str]:
        names = {name.rsplit(".", 1)[-1] for name in self.signatures}
        unknown = set(relation.names) - names
        if unknown:
            raise ContractEvaluationError(
                f"unknown trusted effect names: {sorted(unknown)!r}"
            )
        fields = [
            frozenset(signature.parameters)
            for name, signature in self.signatures.items()
            if not relation.names or name.rsplit(".", 1)[-1] in relation.names
        ]
        return frozenset.intersection(*fields) if fields else frozenset()

    def predicate(
        self, spec: LambdaSpec, *, fields: frozenset[str]
    ) -> Callable[[dict[str, Any]], Any]:
        unknown = spec.fields() - fields
        if unknown:
            raise ContractEvaluationError(
                f"unknown concrete effect fields: {sorted(unknown)!r}"
            )
        _validate_expression(spec.body, spec.arg_name, spec.env)
        env = {name: _literal(value) for name, value in spec.env.items()}
        if self.row_sources is None and any(
            isinstance(value, ColumnRef) for value in env.values()
        ):
            raise ContractEvaluationError(
                "column comparisons require observed row_sources"
            )
        body = _ColumnComparisons().visit(copy.deepcopy(spec.body))
        expression = ast.fix_missing_locations(ast.Expression(body=body))
        code = compile(expression, "<concrete contract>", "eval")
        globals_ = {
            **env,
            "__builtins__": {},
            "len": len,
            "__clauz3_equal__": self.equal,
        }

        def evaluate(row: dict[str, Any]) -> Any:
            return eval(code, globals_, {spec.arg_name: _EffectRow(row)})

        return evaluate

    def equal(self, left: Any, right: Any) -> bool:
        if isinstance(left, ColumnRef) or isinstance(right, ColumnRef):
            if isinstance(left, ColumnRef) and isinstance(right, ColumnRef):
                raise ContractEvaluationError(
                    "column-to-column comparisons are unsupported"
                )
            column, value = (
                (left, right) if isinstance(left, ColumnRef) else (right, left)
            )
            if self.row_sources is None:
                raise ContractEvaluationError(
                    "column comparisons require observed row_sources"
                )
            return any(
                _same_schema(type(row), column.schema)
                and getattr(row, column.field) == value
                for row in self.row_sources
            )
        return bool(left == right)

    def fluent_state(self, fluent: Fluent, facts: list[_Fact]) -> dict[Any, Any]:
        state: dict[Any, Any] = {}
        for fact in facts:
            for update in fluent._effects.get(fact.name, ()):
                key = _resolve_update(update.key, fact.args)
                value = _resolve_update(update.value, fact.args)
                state[key] = value
        return state


def _resolve_update(value: Any, args: dict[str, Any]) -> Any:
    if isinstance(value, _ParamRef):
        if value.name not in args:
            raise ContractEvaluationError(
                f"unknown fluent update parameter {value.name!r}"
            )
        return args[value.name]
    return _literal(value)


def _same_schema(left: type, right: type) -> bool:
    return left is right or (
        left.__module__ == right.__module__
        and left.__qualname__ == right.__qualname__
        and left.__annotations__ == right.__annotations__
    )


_COMPARISONS: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
_EQUALITY: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
}


class _ColumnComparisons(ast.NodeTransformer):
    """Only ColumnRef equality is special; CPython evaluates everything else."""

    def visit_Compare(self, node: ast.Compare) -> ast.expr:
        self.generic_visit(node)
        if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            return node
        call: ast.expr = ast.Call(
            func=ast.Name(id="__clauz3_equal__", ctx=ast.Load()),
            args=[node.left, node.comparators[0]],
            keywords=[],
        )
        if isinstance(node.ops[0], ast.NotEq):
            call = ast.UnaryOp(op=ast.Not(), operand=call)
        return ast.copy_location(call, node)


def _validate_expression(node: ast.AST, arg: str, env: Mapping[str, Any]) -> None:
    """Fail closed before evaluating, including predicates over empty traces."""
    if isinstance(node, ast.Constant):
        _literal(node.value)
        return
    if isinstance(node, ast.List):
        for item in node.elts:
            _validate_expression(item, arg, env)
        return
    if isinstance(node, ast.Name):
        if node.id == arg or node.id not in env:
            raise ContractEvaluationError(f"unsupported contract name {node.id!r}")
        _literal(env[node.id])
        return
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == arg:
            return
    elif isinstance(node, ast.Compare):
        if len(node.ops) == 1 and isinstance(
            node.ops[0],
            (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn),
        ):
            _validate_expression(node.left, arg, env)
            _validate_expression(node.comparators[0], arg, env)
            return
    elif isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        for value in node.values:
            _validate_expression(value, arg, env)
        return
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        _validate_expression(node.operand, arg, env)
        return
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        _validate_expression(node.left, arg, env)
        _validate_expression(node.right, arg, env)
        return
    elif isinstance(node, ast.Call) and len(node.args) == 1 and not node.keywords:
        func = node.func
        if isinstance(func, ast.Name) and func.id == "len":
            _validate_expression(node.args[0], arg, env)
            return
        if isinstance(func, ast.Attribute) and func.attr in {"startswith", "endswith"}:
            _validate_expression(func.value, arg, env)
            _validate_expression(node.args[0], arg, env)
            return
    raise ContractEvaluationError(
        f"unsupported contract expression {type(node).__name__}"
    )


def _literal(value: Any) -> Any:
    if value is None or type(value) in (bool, int, str, float):
        return value
    if isinstance(value, ColumnRef):
        return value
    if type(value) is list:
        return [_literal(item) for item in value]
    if type(value) is tuple:
        return tuple(_literal(item) for item in value)
    if type(value) is dict:
        return {_literal(key): _literal(item) for key, item in value.items()}
    if isinstance(value, ProxySort):
        return _solver_literal(value.expr)
    raise ContractEvaluationError(
        f"unsupported contract literal {type(value).__name__}"
    )


def _solver_literal(expression: Any) -> Any:
    """Decode constants only; never ask a solver to complete symbolic values."""
    value = z3.simplify(expression)
    if z3.is_true(value):
        return True
    if z3.is_false(value):
        return False
    if z3.is_int_value(value):
        return value.as_long()
    if is_string_expr(value):
        try:
            return string_value(value)
        except UnsupportedError as error:
            raise ContractEvaluationError(
                f"nonliteral or unsupported symbolic contract value: {value}"
            ) from error
    if z3.is_seq(value):
        kind = value.decl().kind()
        if kind == z3.Z3_OP_SEQ_EMPTY:
            return []
        if kind == z3.Z3_OP_SEQ_UNIT:
            return [_solver_literal(value.arg(0))]
        if kind == z3.Z3_OP_SEQ_CONCAT:
            return [
                item for child in value.children() for item in _solver_literal(child)
            ]
    raise ContractEvaluationError(
        f"nonliteral or unsupported symbolic contract value: {value}"
    )

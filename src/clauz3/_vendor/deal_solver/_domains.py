"""Python value-domain constraints for externally supplied symbolic values."""

from __future__ import annotations

import typing

import z3

from ._proxies import BoolSort, DictSort, SetSort, StrSort, VarTupleSort
from ._proxies._row import QueryResultSort, RowSort, z3_datatype_for_row
from ._strings import string_domain

if typing.TYPE_CHECKING:
    from ._context import Context
    from ._proxies import ProxySort


def add_value_domain(value: ProxySort, *, ctx: Context) -> None:
    """Constrain a newly materialized value to Python's represented domain."""
    condition = value_domain(value)
    if not z3.is_true(condition):
        domain = BoolSort(condition)
        ctx.given.add(domain)
        # Only free input symbols have an independent domain witness. A
        # domain on a derived expression can itself be contradictory.
        if z3.is_const(value.expr) and value.expr.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            ctx.pure_domains.add(domain)


def value_domain(value: ProxySort) -> z3.BoolRef:
    """Include Unicode invariants recursively, only for actual container items.

    Array encodings also contain absent dictionary entries and nonmembers of
    sets. Those values are not Python data and must not restrict the domain.
    """
    expr = value.expr
    if isinstance(value, StrSort):
        return string_domain(expr)
    if isinstance(value, QueryResultSort):
        index = z3.FreshInt('unicode_row', ctx=expr.ctx)
        domain = _row_domain(value.array_expr[index], value.row_schema)
        if z3.is_true(domain):
            return domain
        present = z3.And(index >= 0, index < value.length_expr)
        return z3.ForAll(index, z3.Implies(present, domain))
    if isinstance(value, RowSort):
        return _row_domain(expr, value.schema)
    if isinstance(value, VarTupleSort) and value.subtypes:
        index = z3.FreshInt('unicode_item', ctx=expr.ctx)
        domain = value_domain(value.subtypes[0].wrap(expr[index]))
        if z3.is_true(domain):
            return domain
        present = z3.And(index >= 0, index < z3.Length(expr))
        return z3.ForAll(index, z3.Implies(present, domain))
    if isinstance(value, DictSort) and value.subtypes:
        key = z3.FreshConst(expr.domain(), prefix='unicode_key')
        item = z3.Select(expr, key)
        key_domain = value_domain(value.subtypes[0].wrap(key))
        item_domain = value_domain(value.subtypes[1].wrap(value.item_sort.value(item)))
        domains = [term for term in (key_domain, item_domain) if not z3.is_true(term)]
        if not domains:
            return z3.BoolVal(True, ctx=expr.ctx)
        return z3.ForAll(
            key, z3.Implies(value.item_sort.exists(item), z3.And(*domains)),
        )
    if isinstance(value, SetSort) and value.subtypes:
        item = z3.FreshConst(expr.domain(), prefix='unicode_member')
        domain = value_domain(value.subtypes[0].wrap(item))
        if z3.is_true(domain):
            return domain
        return z3.ForAll(item, z3.Implies(z3.IsMember(item, expr), domain))
    return z3.BoolVal(True, ctx=expr.ctx)


def _row_domain(expr: z3.ExprRef, schema: type) -> z3.BoolRef:
    datatype = z3_datatype_for_row(schema)
    domains = [
        string_domain(datatype.accessor(0, index)(expr))
        for index, field_type in enumerate(schema.__annotations__.values())
        if field_type is str
    ]
    return z3.And(*domains) if domains else z3.BoolVal(True, ctx=expr.ctx)

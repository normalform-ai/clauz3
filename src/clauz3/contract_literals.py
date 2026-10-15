"""Exact source values for ordinary Python contract builders.

Symbolic proxies deliberately do not implement Python value semantics. Keep
literal arguments out of that representation until a builder has returned its
declarative spec; relation expressions can then encode the values they use.
"""

from collections.abc import Callable
from dataclasses import dataclass

import astroid

from clauz3._vendor.deal_solver._exceptions import UnsupportedError


@dataclass(frozen=True)
class ContractLiteralArgument:
    value: object


def materialize_contract_argument(
    node: astroid.NodeNG,
    *,
    resolve_row: Callable[[astroid.Name], object],
) -> ContractLiteralArgument:
    """Copy a fixed source value, preserving its Python type and contents."""
    try:
        value = _literal_value(node, resolve_row=resolve_row)
    except (TypeError, ValueError) as exc:
        raise UnsupportedError(
            "unsupported contract literal argument", node.as_string(), str(exc)
        ) from exc
    return ContractLiteralArgument(value)


def _literal_value(
    node: astroid.NodeNG, *, resolve_row: Callable[[astroid.Name], object]
) -> object:
    if isinstance(node, astroid.Const):
        if type(node.value) in (str, bytes, int, float, bool, type(None)):
            return node.value
    elif isinstance(node, astroid.UnaryOp) and node.op in {"+", "-"}:
        if isinstance(node.operand, astroid.Const) and isinstance(
            node.operand.value, (int, float)
        ):
            value = node.operand.value
            return +value if node.op == "+" else -value
    elif isinstance(node, astroid.List):
        return [_literal_value(item, resolve_row=resolve_row) for item in node.elts]
    elif isinstance(node, astroid.Tuple):
        return tuple(
            _literal_value(item, resolve_row=resolve_row) for item in node.elts
        )
    elif isinstance(node, astroid.Set):
        return {_literal_value(item, resolve_row=resolve_row) for item in node.elts}
    elif isinstance(node, astroid.Dict):
        return {
            _literal_value(key, resolve_row=resolve_row): _literal_value(
                value, resolve_row=resolve_row
            )
            for key, value in node.items
        }
    elif isinstance(node, astroid.Name):
        return resolve_row(node)
    raise UnsupportedError("unsupported contract literal argument", node.as_string())

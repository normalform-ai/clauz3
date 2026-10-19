"""Exact source values for ordinary Python contract builders.

Symbolic proxies deliberately do not implement Python value semantics. Keep
literal arguments out of that representation until a builder has returned its
declarative spec; relation expressions can then encode the values they use.
"""

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import astroid

from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3.decorators import canonical_target, dotted_name, import_aliases
from clauz3.paths import DirectoryPath, FilePath

PATH_CONSTRUCTORS: dict[str, type[FilePath] | type[DirectoryPath]] = {
    "clauz3.FilePath": FilePath,
    "clauz3.DirectoryPath": DirectoryPath,
}


def _check_path_binding(node: astroid.Call, aliases: Mapping[str, str]) -> None:
    """Require the actual binding to agree with the import-only alias map."""
    head = node.func
    while isinstance(head, astroid.Attribute):
        head = head.expr
    if not isinstance(head, astroid.Name):
        raise ValueError("a typed path must name an imported clauz3 constructor")
    _, bindings = head.lookup(head.name)
    if not bindings:
        raise ValueError("a typed path constructor must be imported from clauz3")
    for binding in bindings:
        if isinstance(binding, astroid.Import):
            targets = {
                asname or name.split(".", 1)[0]: name
                if asname
                else name.split(".", 1)[0]
                for name, asname in binding.names
            }
        elif isinstance(binding, astroid.ImportFrom) and not binding.level:
            targets = {
                asname or name: f"{binding.modname}.{name}"
                for name, asname in binding.names
            }
        else:
            raise ValueError("a typed path constructor cannot be redefined")
        if targets.get(head.name) != aliases.get(head.name, head.name):
            raise ValueError("a typed path constructor has ambiguous imports")


def validate_path_bindings(source: str, locations: set[tuple[int, int]]) -> None:
    """Check source bindings for typed literals accepted by the AST grammar."""
    if not locations:
        return
    module = astroid.parse(source)
    aliases = import_aliases(module)
    for node in module.nodes_of_class(astroid.Call):
        if (node.lineno, node.col_offset) in locations:
            _check_path_binding(node, aliases)


def path_literal(
    node: ast.Call | astroid.Call, aliases: Mapping[str, str]
) -> FilePath | DirectoryPath:
    """Decode only the two built-in constructors; never evaluate source calls."""
    target = canonical_target(dotted_name(node.func), aliases)
    constructor = PATH_CONSTRUCTORS.get(target)
    if constructor is None or node.keywords or len(node.args) != 1:
        raise ValueError("expected FilePath or DirectoryPath with one string literal")
    value = node.args[0]
    if (
        not isinstance(value, ast.Constant | astroid.Const)
        or type(value.value) is not str
    ):
        raise ValueError("a typed path requires one string literal")
    if isinstance(node, astroid.Call):
        _check_path_binding(node, aliases)
    return constructor(value.value)


def fixed_literal_value(node: ast.expr, aliases: Mapping[str, str]) -> object:
    """Decode fixed values without executing source, excluding Row schemas."""
    if isinstance(node, ast.Call):
        return path_literal(node, aliases)
    if isinstance(node, ast.List):
        return [fixed_literal_value(item, aliases) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(fixed_literal_value(item, aliases) for item in node.elts)
    if isinstance(node, ast.Set):
        return {fixed_literal_value(item, aliases) for item in node.elts}
    if isinstance(node, ast.Dict):
        if any(key is None for key in node.keys):
            raise ValueError("literal unpacking is unsupported")
        return {
            fixed_literal_value(key, aliases): fixed_literal_value(value, aliases)
            for key, value in zip(node.keys, node.values, strict=True)
            if key is not None
        }
    return ast.literal_eval(node)


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
    elif isinstance(node, astroid.Call):
        return path_literal(node, import_aliases(node.root()))
    raise UnsupportedError("unsupported contract literal argument", node.as_string())

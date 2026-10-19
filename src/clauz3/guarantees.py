"""The grammar of ``@clauz3.guarantee(...)``, and the names it resolves.

A guarantee is the sentence a human approves. The approval request shows
``ast.unparse`` of whatever the agent wrote, so the text has to *mean*
something fixed: it must name an obligation the trusted layer defined, over
arguments the reader can see in full. Anything else is a claim the reader
cannot evaluate -- and, worse, an expression whose value need not be a
proposition at all. ``ProxySort.m_bool`` defaults to true, so a guarantee that
evaluates to a function, a string, or an integer used to discharge itself and
report ``proved!``.

The grammar accepted here is therefore deliberately small::

    guarantee  := <registered @contract> "(" [ argument {"," argument} ] ")"
    argument   := literal | row_class | typed_path
    literal    := str | bytes | int | float | bool | None | -number
                | list/tuple/set of literal
                | dict of literal -> literal
    typed_path := clauz3.FilePath(str) | clauz3.DirectoryPath(str)
    row_class  := a name bound to a clauz3.Row subclass

"Registered" means decorated with :func:`clauz3.spec.contract`, which only
trusted roots may do (``prover.validate_untrusted_source`` refuses
``@contract`` in an entry file). A helper the agent defined next to ``main``
therefore cannot pass, however much it looks like a contract.

Both readers of guarantee decorators -- the prover and the approval request --
come through here, so neither can accept a guarantee the other would refuse.
Decorators are matched by the target they resolve to through the module's
import bindings (:mod:`clauz3.decorators`), not by their spelling, so
``import clauz3 as c; @c.guarantee(...)`` is checked like any other form and
cannot slip past the grammar (issue #129).
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable, Iterator
from pathlib import Path
from textwrap import dedent

from clauz3 import decorators as decorators_mod
from clauz3.contract_literals import (
    PATH_CONSTRUCTORS,
    fixed_literal_value,
    path_literal,
    validate_path_bindings,
)
from clauz3.row import row_class_by_name
from clauz3.spec import REGISTERED_CONTRACTS

_HINT = (
    "a guarantee must call a @contract helper from a trusted root with "
    "literal arguments, e.g. @clauz3.guarantee(contracts.only(['bob@example.com']))"
)


class GuaranteeError(Exception):
    """A ``@clauz3.guarantee(...)`` decorator is not a well-formed guarantee."""


def guarantee_expressions(
    *,
    source: str,
    source_name: str = "<string>",
    target: str | None = None,
) -> list[ast.expr]:
    """Return the guarantee arguments written on *target*, after checking all.

    Every guarantee decorator in *source* is checked against the grammar, not
    only the target's: the entry file is the agent's whole claim surface, and a
    malformed guarantee anywhere in it is a bug worth reporting. A named target
    selects only a direct module-level function, never a same-named nested
    function or method. ``target=None`` returns all checked expressions for
    whole-file grammar validation. Raises :class:`GuaranteeError` on the first
    violation.
    """

    module = ast.parse(dedent(source), filename=source_name)
    aliases = decorators_mod.import_aliases(module)
    entry_functions = {
        node for node in module.body if isinstance(node, ast.FunctionDef)
    }
    selected = []
    path_locations: set[tuple[int, int]] = set()
    for func in _function_defs(module):
        for expression in _guarantee_arguments(
            func, aliases=aliases, source_name=source_name
        ):
            _check_expression(expression, aliases=aliases, source_name=source_name)
            path_locations.update(
                (node.lineno, node.col_offset)
                for node in ast.walk(expression)
                if isinstance(node, ast.Call)
                and decorators_mod.canonical_target(
                    decorators_mod.dotted_name(node.func), aliases
                )
                in PATH_CONSTRUCTORS
            )
            if target is None or (func in entry_functions and func.name == target):
                selected.append(expression)
    try:
        validate_path_bindings(dedent(source), path_locations)
    except ValueError as exc:
        raise GuaranteeError(f"{source_name}: {exc}") from exc
    return selected


def guarantee_texts(
    *,
    source: str,
    target: str,
    source_name: str = "<string>",
) -> list[str]:
    """Return *target*'s guarantees as source text, for the approval request."""

    return [
        ast.unparse(expression)
        for expression in guarantee_expressions(
            source=source,
            source_name=source_name,
            target=target,
        )
    ]


def validate_guarantees(*, source: str, source_name: str = "<string>") -> None:
    """Raise :class:`GuaranteeError` unless every guarantee fits the grammar."""

    guarantee_expressions(source=source, source_name=source_name)


def contract_documentation(*, source: str, target: str) -> dict[str, str]:
    """Snapshot docstrings from the currently selected, verified contracts.

    Call inside the trusted-layer context. Ambient registrations are not
    authority: their model must be the one selected in FUNCTIONS. Never import
    a module based on an expression submitted for display.
    """
    documentation = {}
    for expression, owner in _contract_calls(source=source, target=target):
        doc = inspect.getdoc(owner)
        if doc:
            documentation[ast.unparse(expression)] = doc
    return documentation


def contract_descriptions(*, source: str, target: str) -> dict[str, dict[str, object]]:
    """Generate descriptions from selected trusted metadata and literal values."""
    from clauz3.contract_metadata import describe_call

    aliases = decorators_mod.import_aliases(ast.parse(dedent(source)))
    descriptions = {}
    for expression, owner in _contract_calls(source=source, target=target):
        try:
            args = [fixed_literal_value(arg, aliases) for arg in expression.args]
            kwargs = {
                kw.arg: fixed_literal_value(kw.value, aliases)
                for kw in expression.keywords
                if kw.arg is not None
            }
        except (ValueError, TypeError):
            continue  # Row types have no scalar representation in CNL v1.
        description = describe_call(owner, args=args, kwargs=kwargs)
        if description is not None:
            descriptions[ast.unparse(expression)] = description
    return descriptions


def contract_identities(
    *, source: str, target: str, roots: list[Path], sources: dict[str, str]
) -> dict[str, dict[str, object]]:
    """Snapshot resolved identities inside the selected, verified layer context."""
    from clauz3.contract_identity import identify_call

    aliases = decorators_mod.import_aliases(ast.parse(dedent(source)))
    return {
        ast.unparse(expression): identify_call(
            owner, expression, aliases=aliases, roots=roots, sources=sources
        )
        for expression, owner in _contract_calls(source=source, target=target)
    }


def _contract_calls(
    *, source: str, target: str
) -> Iterator[tuple[ast.Call, Callable[..., object]]]:
    from clauz3._vendor.deal_solver._funcs._registry import FUNCTIONS, REGISTRATIONS

    aliases = decorators_mod.import_aliases(ast.parse(dedent(source)))
    owners = {
        record.name: record.owner
        for record in REGISTRATIONS
        if record.is_contract and FUNCTIONS.get(record.name) is record.model
    }
    for expression in guarantee_expressions(source=source, target=target):
        assert isinstance(expression, ast.Call)
        callee = decorators_mod.canonical_target(
            decorators_mod.dotted_name(expression.func), aliases
        )
        if owner := owners.get(callee):
            yield expression, owner


def _function_defs(module: ast.Module) -> Iterator[ast.FunctionDef]:
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef):
            yield node


def _guarantee_arguments(
    func: ast.FunctionDef,
    *,
    aliases: dict[str, str],
    source_name: str,
) -> Iterator[ast.expr]:
    """Yield the single argument of each guarantee decorator on *func*.

    Decorators are recognised by what they resolve to under *aliases*, so an
    aliased spelling is checked rather than skipped. A guarantee decorator that
    is not a call, or that carries anything other than exactly one positional
    argument, is refused rather than skipped: the keyword form
    ``@clauz3.guarantee(_predicate=...)`` used to be dropped in silence by both
    the prover and the approval request.
    """

    for decorator in func.decorator_list:
        if not decorators_mod.is_guarantee(decorator, aliases):
            continue
        spelled = decorators_mod.dotted_name(decorator)
        if not isinstance(decorator, ast.Call):
            raise GuaranteeError(
                f"{source_name}: @{spelled} on {func.name!r} must be "
                f"called with a guarantee; {_HINT}",
            )
        if decorator.keywords or len(decorator.args) != 1:
            raise GuaranteeError(
                f"{source_name}: @{spelled} on {func.name!r} takes "
                "exactly one positional argument, "
                f"got {ast.unparse(decorator)}; {_HINT}",
            )
        yield decorator.args[0]


def _check_expression(
    expression: ast.expr,
    *,
    aliases: dict[str, str],
    source_name: str,
) -> None:
    text = ast.unparse(expression)
    if not isinstance(expression, ast.Call):
        raise GuaranteeError(
            f"{source_name}: guarantee {text} is not a contract call; {_HINT}",
        )
    spelled = decorators_mod.dotted_name(expression.func)
    callee = decorators_mod.canonical_target(spelled, aliases)
    if callee not in REGISTERED_CONTRACTS:
        raise GuaranteeError(
            f"{source_name}: guarantee {text} does not call a registered "
            f"@contract ({callee or spelled!r} is not one). "
            f"Registered contracts: {sorted(REGISTERED_CONTRACTS)}",
        )
    arguments: list[ast.expr] = list(expression.args)
    arguments += [keyword.value for keyword in expression.keywords]
    if any(keyword.arg is None for keyword in expression.keywords):
        raise GuaranteeError(
            f"{source_name}: guarantee {text} unpacks its arguments; {_HINT}",
        )
    for argument in arguments:
        if not _is_fixed_value(argument, aliases):
            raise GuaranteeError(
                f"{source_name}: guarantee {text} has non-literal argument "
                f"{ast.unparse(argument)}; contract arguments must be literals, "
                "literal containers, typed FilePath/DirectoryPath literals, "
                "or clauz3.Row classes",
            )


def _is_fixed_value(node: ast.expr, aliases: dict[str, str]) -> bool:
    """Whether *node* denotes a value a reader of the source can see in full."""

    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        return isinstance(node.operand, ast.Constant)
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return all(_is_fixed_value(element, aliases) for element in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            key is not None and _is_fixed_value(key, aliases) for key in node.keys
        ) and (all(_is_fixed_value(value, aliases) for value in node.values))
    if isinstance(node, ast.Call):
        try:
            path_literal(node, aliases)
        except (TypeError, ValueError):
            return False
        return True
    if isinstance(node, ast.Starred):
        return False
    if isinstance(node, ast.Name):
        return row_class_by_name(node.id) is not None
    return False

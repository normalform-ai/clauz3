"""Names reserved for the imports clauz3 injects into a proved copy.

Two rewriters state an obligation the submitted program did not write:
:func:`clauz3.prover._inject_required_guarantees` for a domain's ``required``
contracts and :func:`clauz3.approvals.policy._conjoin` for an approval-policy
clause. Each prepends ``import <module> as <alias>`` and decorates the target
with ``@clauz3.guarantee(<alias>.<contract>(...))``.

Prepending is not enough to own the alias. Python binds a name to whatever was
imported last, so a program that later wrote ``import other.contracts as
<alias>`` moved the obligation to ``other``'s same-named contract, and the one
the trusted layer or the policy admin chose was never proved.

Aliases therefore come from a reserved namespace that submitted source may not
spell at all, as a binding or as a use. Refusing is fail-closed and loses
nothing: no program has a reason to write these names. The rewritten source is
checked again before it is proved, so a rebinding the first check did not
anticipate is a refusal too, never a different proof.
"""

import ast
from collections.abc import Iterator

from clauz3.decorators import import_aliases

#: Every identifier clauz3 synthesises in a proved copy starts with this.
RESERVED_PREFIX = "_clauz3_"


class ReservedNameError(ValueError):
    """Submitted source spells a reserved name, or rebinds an injected import."""


def reserved_names(tree: ast.AST, *, prefix: str = RESERVED_PREFIX) -> list[str]:
    """The identifiers in ``tree`` that start with ``prefix``, sorted.

    Any occurrence counts: an import alias, an assignment, a parameter, a
    ``def`` or ``class``, ``global``/``nonlocal``, an attribute, a keyword, or
    a plain use. String constants are data, not names.

    >>> reserved_names(ast.parse("import json as _clauz3_x\\ny = '_clauz3_z'"))
    ['_clauz3_x']
    >>> reserved_names(ast.parse("def f(a): return a._clauz3_y"))
    ['_clauz3_y']
    >>> reserved_names(ast.parse("__clauz3_x = clauz3_y = 1"))
    []
    """
    return sorted(
        {name for _, name in _identifiers(tree) if name.startswith(prefix)},
    )


def _identifiers(tree: ast.AST) -> Iterator[tuple[ast.AST, str]]:
    """Every ``(node, identifier)`` the tree spells, dotted names split up.

    Read from each node's own string fields rather than from a list of node
    types, so a statement form added to the grammar later is covered without
    being named here. ``Constant`` is the one node whose strings are values.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            continue
        for _, value in ast.iter_fields(node):
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, str):
                    for part in item.split("."):
                        yield node, part


class InjectedImports:
    """Allocate, inject and verify the aliases one rewriter adds to a module.

    Construct it on the parsed source *before* rewriting: it refuses a tree
    that already spells a name this rewriter could allocate. ``alias`` hands
    out one name per module, and ``render`` prepends the imports, unparses the
    tree and confirms that each alias is bound exactly once, to its module.
    """

    def __init__(self, tree: ast.Module, *, kind: str) -> None:
        self._tree = tree
        self._prefix = f"{RESERVED_PREFIX}{kind}_"
        self._aliases: dict[str, str] = {}
        taken = reserved_names(tree, prefix=self._prefix)
        if taken:
            raise ReservedNameError(reserved_message(taken))

    def alias(self, module: str) -> str:
        """The reserved name ``module`` is imported under in the proved copy."""
        return self._aliases.setdefault(module, f"{self._prefix}{len(self._aliases)}")

    def render(self) -> str:
        """The rewritten source, with its injected imports verified."""
        imports: list[ast.stmt] = [ast.Import(names=[ast.alias(name="clauz3")])]
        imports += [
            ast.Import(names=[ast.alias(name=module, asname=alias)])
            for module, alias in self._aliases.items()
        ]
        self._tree.body[0:0] = imports
        ast.fix_missing_locations(self._tree)
        rendered = ast.unparse(self._tree)
        self._verify(ast.parse(rendered))
        return rendered

    def _verify(self, tree: ast.Module) -> None:
        resolved = import_aliases(tree)
        for module, alias in self._aliases.items():
            bindings = sum(
                name == alias
                and not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load))
                for node, name in _identifiers(tree)
            )
            if bindings != 1 or resolved.get(alias) != module:
                raise ReservedNameError(
                    f"the injected import of {module} as {alias} is rebound by "
                    "the submitted source, so the contract it states would not "
                    "be the one proved"
                )


def reserved_message(names: list[str]) -> str:
    """How a refusal names the reserved identifiers it found."""
    return (
        f"{', '.join(names)}: names starting with {RESERVED_PREFIX!r} are "
        "reserved for the imports clauz3 adds to the proved copy of a program; "
        "rename them"
    )

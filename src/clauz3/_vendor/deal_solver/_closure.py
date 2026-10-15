"""Refuse closure bindings that static inference cannot model at call time."""

from __future__ import annotations

import astroid

from ._exceptions import UnsupportedError


def check_static_closure_name(node: astroid.Name) -> None:
    """Check a name only after lookup in the symbolic scope found no value.

    Function calls do not carry lexical closure cells. Astroid's filtered
    lookup can instead select a later enclosing assignment, so it is safe to
    infer a captured callable only from an unconditional, stable declaration.
    Inspect lexical locals, including declarations hidden by filtered lookup.
    """
    owner = _lexical_owner(node)
    if owner is None or owner is node.scope():
        return
    _check_binding(owner, node.name, node, set())


def _lexical_owner(node: astroid.Name) -> astroid.FunctionDef | astroid.Lambda | None:
    scope = node.scope()
    while not isinstance(scope, astroid.Module):
        if isinstance(scope, (astroid.FunctionDef, astroid.Lambda)):
            # These declarations change lexical ownership. They have no cell
            # model here, and must not be mistaken for an ordinary capture.
            for declaration in scope.nodes_of_class((astroid.Global, astroid.Nonlocal)):
                if declaration.scope() is scope and node.name in declaration.names:
                    raise UnsupportedError("unsupported closure binding", node.name)
            if node.name in scope.locals:
                return scope
        if scope.parent is None:
            break
        scope = scope.parent.scope()
    return None


def _check_binding(
    owner: astroid.FunctionDef | astroid.Lambda,
    name: str,
    use: astroid.NodeNG,
    seen: set[tuple[astroid.NodeNG, str]],
) -> None:
    key = (owner, name)
    if key in seen:
        raise UnsupportedError("cyclic closure binding", name)
    bindings = owner.locals.get(name, ())
    if len(bindings) != 1:
        raise UnsupportedError("reassigned closure binding", name)
    binding = bindings[0]
    statement = binding.parent if isinstance(binding, astroid.AssignName) else binding

    # A declaration inside a branch/loop may not run, or may run repeatedly.
    # Requiring it before the nested scope also excludes reads before binding
    # and closures whose value depends on when their caller invokes them.
    boundary = use
    while boundary.parent is not owner:
        if boundary.parent is None:
            raise UnsupportedError("unsupported closure binding", name)
        boundary = boundary.parent
    if (
        not isinstance(owner, astroid.FunctionDef)
        or statement is None
        or statement.parent is not owner
        or statement not in owner.body
        or statement.end_lineno is None
        or statement.end_lineno >= boundary.lineno
    ):
        raise UnsupportedError("unsupported closure binding", name)

    if isinstance(binding, (astroid.Import, astroid.ImportFrom, astroid.FunctionDef)):
        return
    if isinstance(binding, astroid.AssignName) and isinstance(statement, astroid.Assign):
        value = statement.value
        if isinstance(value, astroid.Name):
            alias_owner = _lexical_owner(value)
            if alias_owner is not None:
                # Keep checking even when the RHS is in its owner's scope:
                # inference of an alias must not hide a mutable captured cell.
                _check_binding(alias_owner, value.name, value, seen | {key})
            return
    raise UnsupportedError("unsupported closure binding", name)

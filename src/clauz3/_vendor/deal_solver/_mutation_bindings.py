"""Refuse collection writes whose receiver storage cannot be updated safely."""

from __future__ import annotations

import astroid

from ._exceptions import UnsupportedError


PARAMETER_MUTATION_ERROR = 'mutating a function parameter or its alias is not supported'
CAPTURED_MUTATION_ERROR = 'mutating a captured collection is not supported'
BINDING_MUTATION_ERROR = 'unsupported mutation receiver binding'
_MAX_BINDING_NODES = 256


def require_local_mutation_receiver(use: astroid.Name) -> None:
    # Values are copied into detached helper contexts, without shared heap
    # identity. Track source dependencies rather than proxy identity: branch
    # joins and calls can build a new proxy for the same runtime collection.
    active: set[tuple[astroid.NodeNG, str]] = set()
    checked: set[tuple[astroid.NodeNG, str]] = set()
    remaining = _MAX_BINDING_NODES
    mutation_scope = use.scope()

    def visit(node: astroid.NodeNG) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            raise UnsupportedError(BINDING_MUTATION_ERROR, 'binding budget exceeded')
        if not isinstance(node, astroid.Name):
            for child in node.get_children():
                visit(child)
            return
        owner = node.scope()
        while not isinstance(owner, astroid.Module) and node.name not in owner.locals:
            if owner.parent is None:
                raise UnsupportedError(BINDING_MUTATION_ERROR, 'unknown binding')
            owner = owner.parent.scope()
        if isinstance(owner, astroid.Module):
            # Module captures have their own static-binding checks; this gate
            # concerns function parameters and enclosing function storage.
            return
        if owner is not mutation_scope:
            # Lambda/comprehension evaluation discards its scope layer. A
            # write to an outer receiver (including an iterator alias) would
            # update only that child, not the collection observed by Python.
            raise UnsupportedError(CAPTURED_MUTATION_ERROR, use.name)
        key = (owner, node.name)
        if key in active:
            raise UnsupportedError(BINDING_MUTATION_ERROR, 'cyclic binding')
        if key in checked:
            return
        active.add(key)
        for binding in owner.locals.get(node.name, ()):
            if isinstance(binding, (astroid.FunctionDef, astroid.Import, astroid.ImportFrom)):
                continue
            statement = binding.parent
            while isinstance(statement, (astroid.Tuple, astroid.List)):
                statement = statement.parent
            if isinstance(statement, astroid.Arguments):
                raise UnsupportedError(PARAMETER_MUTATION_ERROR, use.name)
            if isinstance(statement, (astroid.Assign, astroid.AnnAssign)):
                if statement.value is None:
                    raise UnsupportedError(BINDING_MUTATION_ERROR, 'unknown binding')
                visit(statement.value)
            elif isinstance(statement, astroid.AugAssign):
                # An augmented assignment can preserve a parameter alias;
                # its earlier lexical binding is checked in the same walk.
                visit(statement.value)
            elif isinstance(statement, (astroid.For, astroid.Comprehension)):
                visit(statement.iter)
            else:
                raise UnsupportedError(BINDING_MUTATION_ERROR, 'unknown binding')
        active.remove(key)
        checked.add(key)

    visit(use)

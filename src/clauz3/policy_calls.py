"""Resolve policy triggers through the entry's lexical call bindings.

Use Astroid's source-only name lookup, as the prover does, without inferring
or importing callees. Imports in a different scope cannot rename a call.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TypeAlias

from astroid import nodes

from clauz3.policy_imports import SourceModules, parse_source
from clauz3.policy_stdlib import ModeledStdlib, modeled_stdlib

_Binding: TypeAlias = (
    str | nodes.FunctionDef | nodes.Lambda | nodes.Module | ModeledStdlib
)

# Not a Python identifier, so it cannot collide with a trusted effect name.
# Every policy must be considered when lexical lookup cannot bound a callee.
DYNAMIC_CALL = "<dynamic call>"


def transitive_calls(
    source: str,
    target: str,
    *,
    import_roots: Iterable[Path] = (),
    trusted_roots: Iterable[Path] = (),
) -> set[str]:
    """Over-approximate calls reachable from the selected module function.

    Branch alternatives are retained. Local aliases and nested helpers resolve
    in their lexical scopes; unused definitions do not contribute calls.
    Duplicate module helpers keep their final binding, matching name lookup
    in the prover and the interpreter's module initialization. An unresolved
    callee contributes ``DYNAMIC_CALL``, which activates every domain policy.
    Imported helpers and re-exports resolve only inside ``import_roots``;
    trusted effect implementations under ``trusted_roots`` are not traversed.
    Missing roots or unsupported source resolution use the same fallback.
    """
    module = parse_source(source)
    if module is None:
        return {DYNAMIC_CALL}
    resolver = SourceModules(import_roots, trusted_roots)
    functions = {
        node.name: node for node in module.body if isinstance(node, nodes.FunctionDef)
    }
    entry = functions.get(target)
    if entry is None:
        return set()
    used: set[str] = set()
    pending: list[nodes.FunctionDef | nodes.Lambda] = [entry]
    visited: set[nodes.FunctionDef | nodes.Lambda] = set()
    while pending:
        function = pending.pop()
        if function in visited:
            continue
        visited.add(function)
        body = [function.body] if isinstance(function, nodes.Lambda) else function.body
        for statement in body:
            for expression in _expressions(statement):
                if isinstance(expression, nodes.Call):
                    bindings = _call_bindings(expression.func, set(), resolver)
                elif isinstance(
                    expression, (nodes.Name, nodes.Attribute, nodes.Lambda)
                ):
                    parent = expression.parent
                    if (
                        isinstance(parent, nodes.Call) and parent.func is expression
                    ) or (
                        isinstance(parent, nodes.Attribute)
                        and parent.expr is expression
                    ):
                        # The complete callee/attribute is handled as a unit;
                        # its module qualifier is not another escaping value.
                        continue
                    # A callable passed to an imported helper, returned, or
                    # stored in a container can escape direct call lookup.
                    # Follow known callable references without treating every
                    # ordinary argument/return value as an unknown call.
                    bindings = _call_bindings(
                        expression, set(), resolver, as_value=True
                    )
                else:
                    continue
                for binding in bindings:
                    if isinstance(binding, str):
                        used.add(binding)
                    elif isinstance(binding, (nodes.Module, ModeledStdlib)):
                        # A module itself is not a callable; its attributes are
                        # resolved separately.
                        if isinstance(expression, nodes.Call):
                            used.add(DYNAMIC_CALL)
                    else:
                        if isinstance(binding, nodes.FunctionDef):
                            used.add(binding.name)
                            if resolver.is_boundary(binding):
                                continue
                        pending.append(binding)
    return used


def _expressions(node: nodes.NodeNG) -> Iterator[nodes.NodeNG]:
    """Walk executed expressions without entering a new function/class body."""
    yield node
    if isinstance(node, (nodes.FunctionDef, nodes.Lambda)):
        # Defining a function evaluates defaults (and decorators), not its body.
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                yield from _expressions(default)
        if isinstance(node, nodes.FunctionDef) and node.decorators is not None:
            yield from _expressions(node.decorators)
        return
    if isinstance(node, nodes.ClassDef):
        return
    for child in node.get_children():
        yield from _expressions(child)


def _call_bindings(
    expression: nodes.NodeNG,
    seen: set[nodes.NodeNG],
    resolver: SourceModules,
    *,
    as_value: bool = False,
) -> Iterator[_Binding]:
    if expression in seen:
        if not as_value:
            yield DYNAMIC_CALL
        return
    seen = seen | {expression}
    if isinstance(expression, nodes.Attribute):
        # The attribute is already the trailing name. A bare-name import alias
        # with the same spelling has no bearing on module.record(...).
        yield expression.attrname
        receivers = list(_call_bindings(expression.expr, seen, resolver, as_value=True))
        for receiver in receivers:
            if isinstance(receiver, (nodes.Module, ModeledStdlib)):
                yield from _export_bindings(
                    receiver, expression.attrname, seen, resolver, as_value=as_value
                )
            elif receiver == DYNAMIC_CALL:
                yield DYNAMIC_CALL
        if expression.attrname == "__call__":
            # Explicit invocation has the same target as calling the receiver.
            yield from _call_bindings(
                expression.expr, seen, resolver, as_value=as_value
            )
    elif isinstance(expression, nodes.Lambda):
        yield expression
    elif isinstance(expression, nodes.Name):
        scope, bindings = expression.lookup(expression.name)
        bindings = list(bindings)
        for local_scope in (scope, expression.scope()):
            # A loop can carry a later assignment into its next iteration, and
            # a closure can be called before or after an enclosing assignment.
            # Source-position lookup alone cannot determine that timing. Keep
            # every binding in this lexical function scope, never siblings'.
            # Also include local declarations when lookup falls through to the
            # module because a local assignment follows this source position.
            if isinstance(local_scope, (nodes.FunctionDef, nodes.Lambda)):
                bindings.extend(local_scope.locals.get(expression.name, ()))
        if not bindings and not as_value:
            yield expression.name
            yield DYNAMIC_CALL
        for binding in dict.fromkeys(bindings):
            yield from _binding_targets(
                binding, expression.name, seen, resolver, as_value=as_value
            )
    elif not as_value:
        # A callback parameter, factory result, subscript, or conditional may
        # still evaluate to a trusted callable. An absent syntactic name is
        # uncertainty, not evidence that no required obligation applies.
        yield DYNAMIC_CALL


def _binding_targets(
    binding: nodes.NodeNG,
    name: str,
    seen: set[nodes.NodeNG],
    resolver: SourceModules,
    *,
    as_value: bool,
) -> Iterator[_Binding]:
    if isinstance(binding, nodes.ImportFrom):
        if binding in seen:
            yield DYNAMIC_CALL
            return
        module_name = resolver.imported_module(binding)
        originals = [
            original for original, alias in binding.names if (alias or original) == name
        ]
        for original in originals:
            yield original
            # Builtin constructors are known without importing or inspecting
            # ambient modules. User bindings with those names are not exempt.
            if module_name == "builtins" and original in {
                "str",
                "int",
                "float",
                "bool",
                "list",
                "dict",
                "set",
                "tuple",
                "range",
            }:
                continue
            module = _resolve_module(module_name, resolver)
            if module is None:
                yield DYNAMIC_CALL
            else:
                yield from _export_bindings(
                    module, original, seen | {binding}, resolver, as_value=as_value
                )
        if not originals:
            yield DYNAMIC_CALL
    elif isinstance(binding, nodes.Import):
        for original, alias in binding.names:
            if (alias or original.split(".")[0]) == name:
                module = _resolve_module(original if alias else name, resolver)
                yield module if module is not None else DYNAMIC_CALL
    elif isinstance(binding, nodes.FunctionDef):
        yield binding
    elif isinstance(binding, nodes.ClassDef) and binding.root().name == "builtins":
        yield binding.name
    elif isinstance(binding, nodes.AssignName) and isinstance(
        binding.parent, (nodes.Assign, nodes.AnnAssign)
    ):
        value = binding.parent.value
        if value is not None:
            yield from _call_bindings(value, seen, resolver, as_value=as_value)
        elif not as_value:
            yield DYNAMIC_CALL
    elif not as_value:
        yield name
        yield DYNAMIC_CALL


def _resolve_module(
    name: str, resolver: SourceModules
) -> nodes.Module | ModeledStdlib | None:
    module = resolver.load(name)
    if module is not None:
        return module
    return modeled_stdlib(name, resolver.import_roots)


def _export_bindings(
    module: nodes.Module | ModeledStdlib,
    name: str,
    seen: set[nodes.NodeNG],
    resolver: SourceModules,
    *,
    as_value: bool,
) -> Iterator[_Binding]:
    if isinstance(module, ModeledStdlib):
        yield name if module.knows(name, as_value=as_value) else DYNAMIC_CALL
        return
    bindings = [
        binding for binding in module.locals.get(name, ()) if binding not in seen
    ]
    if not bindings:
        # ``from . import helpers`` in __init__.py binds the child module,
        # rather than recursively re-exporting its own pending import.
        child = resolver.load(f"{module.name}.{name}") if module.package else None
        yield child if child is not None else DYNAMIC_CALL
        return
    for binding in bindings:
        yield from _binding_targets(binding, name, seen, resolver, as_value=as_value)

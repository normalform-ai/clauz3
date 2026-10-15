"""Reject selected theorem names with ambiguous static module bindings.

This checks binding syntax without evaluating module statements. Calls that
mutate a namespace indirectly remain part of module-execution validation.
"""

from __future__ import annotations

import ast

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_BindingNode = ast.stmt | ast.expr | ast.pattern | ast.ExceptHandler


def target_binding_error(module: ast.Module, *, target: str | None) -> str | None:
    """Return a diagnostic when a selected function's binding is not unique."""
    bindings = _ModuleBindings()
    bindings.visit(module)
    if bindings.wildcard:
        return (
            "wildcard imports are not allowed; their module-level target "
            "bindings cannot be determined statically"
        )
    selected = sorted(bindings.functions) if target is None else [target]
    # Async definitions bind names too, but required-contract injection and
    # guarantee extraction support only synchronous entries, as does run.
    direct_functions = {
        node for node in module.body if isinstance(node, ast.FunctionDef)
    }
    for name in selected:
        sites = bindings.sites.get(name, [])
        if not sites:
            # Preserve the existing no-theorem outcome for a missing target.
            continue
        if len(sites) > 1:
            lines = ", ".join(str(node.lineno) for node in sites)
            return (
                f"target {name!r} is bound more than once at module level "
                f"(lines {lines}); prove and run could resolve different definitions"
            )
        if sites[0] not in direct_functions:
            return (
                f"target {name!r} must be a synchronous function defined directly "
                f"at module level (line {sites[0].lineno}); "
                "conditional, async, or non-function "
                "bindings cannot select a theorem"
            )
    return None


class _ModuleBindings(ast.NodeVisitor):
    """Collect binders in module scope, including explicit class globals."""

    def __init__(self) -> None:
        self.sites: dict[str, list[_BindingNode]] = {}
        self.functions: set[str] = set()
        self.wildcard = False
        # None means module scope; a set means a class namespace whose listed
        # names were explicitly declared global. Function bodies are skipped.
        self.class_globals: set[str] | None = None

    def _bind(self, name: str, node: _BindingNode, *, function: bool = False) -> None:
        if self.class_globals is not None and name not in self.class_globals:
            return
        self.sites.setdefault(name, []).append(node)
        if function:
            self.functions.add(name)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(node.id, node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._bind(alias.asname or alias.name.partition(".")[0], node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.wildcard = True
            else:
                self._bind(alias.asname or alias.name, node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # A bare annotation adds metadata without replacing the named object.
        if node.value is not None:
            self.visit(node.target)
            self.visit(node.value)
        self.visit(node.annotation)

    def _visit_arguments(self, arguments: ast.arguments) -> None:
        for default in [*arguments.defaults, *arguments.kw_defaults]:
            if default is not None:
                self.visit(default)
        for argument in [
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            arguments.vararg,
            arguments.kwarg,
        ]:
            if argument is not None and argument.annotation is not None:
                self.visit(argument.annotation)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._bind(node.name, node, function=True)
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_arguments(node.args)
        if node.returns is not None:
            self.visit(node.returns)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_arguments(node.args)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._bind(node.name, node)
        for expression in [*node.decorator_list, *node.bases]:
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)
        previous = self.class_globals
        self.class_globals = _class_global_names(node)
        for statement in node.body:
            self.visit(statement)
        self.class_globals = previous

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            # The handler both assigns and eventually deletes its exception
            # name, so it is not a stable selected-function binding.
            self._bind(node.name, node)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name is not None:
            self._bind(node.name, node)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name is not None:
            self._bind(node.name, node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self._bind(node.rest, node)
        self.generic_visit(node)

    def _visit_comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        # Iteration targets belong to the comprehension scope. Assignment
        # expressions in its other expressions bind the containing scope.
        for generator in node.generators:
            self.visit(generator.iter)
            self._visit_comprehension_target(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    def _visit_comprehension_target(self, node: ast.expr) -> None:
        if isinstance(node, (ast.Tuple, ast.List)):
            for element in node.elts:
                self._visit_comprehension_target(element)
        elif isinstance(node, ast.Starred):
            self._visit_comprehension_target(node.value)
        elif isinstance(node, ast.Attribute):
            self.visit(node.value)
        elif isinstance(node, ast.Subscript):
            self.visit(node.value)
            self.visit(node.slice)


def _class_global_names(node: ast.ClassDef) -> set[str]:
    """Find declarations in this class scope without entering nested scopes."""
    names: set[str] = set()
    pending: list[ast.AST] = list(node.body)
    while pending:
        child = pending.pop()
        if isinstance(child, ast.Global):
            names.update(child.names)
        elif not isinstance(child, (*_FUNCTIONS, ast.ClassDef, ast.Lambda)):
            pending.extend(ast.iter_child_nodes(child))
    return names

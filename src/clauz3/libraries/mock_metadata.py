"""Descriptive mock declarations, read from source without importing libraries.

This is deliberately absent from authorization and proof decisions. A library
author can declare a mock whose body performs real effects; the label records
the declaration, never certifies the implementation or its dependencies.
"""

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, TypedDict

from clauz3.decorators import (
    import_aliases,
    is_mock,
    is_trusted_effect,
    unfold_chain,
)


class MockDeclarations(TypedDict):
    kind: Literal["mock", "mixed", "unmarked", "unknown"]
    declared_mock: list[str]
    unmarked: list[str]
    errors: list[str]


def _module_functions(node: ast.AST) -> Iterator[ast.FunctionDef]:
    """Include module-level control flow, without entering function/class scopes."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef):
            yield child
        elif not isinstance(child, ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            yield from _module_functions(child)


def mock_declarations(project: Path | None, roots: list[Path]) -> MockDeclarations:
    """Summarize statically declared effects in selected roots, not their purity."""
    result: MockDeclarations = {
        "kind": "unmarked",
        "declared_mock": [],
        "unmarked": [],
        "errors": [],
    }
    for root in roots:
        if root.is_symlink() or not root.is_dir():
            result["errors"].append(f"Cannot inspect library root: {root.name}")
            continue
        for path in sorted(root.rglob("*.py")):
            try:
                relative = (
                    path.relative_to(project)
                    if project is not None and path.is_relative_to(project)
                    else path
                )
                if path.is_symlink() or not path.resolve().is_relative_to(
                    root.resolve()
                ):
                    raise ValueError("symbolic links are not inspected")
                module = ast.parse(path.read_text(), filename=str(relative))
            except (OSError, ValueError, SyntaxError) as exc:
                result["errors"].append(f"{path.name}: {exc}")
                continue
            aliases = import_aliases(module)
            for node in _module_functions(module):
                if not is_trusted_effect(node, aliases):
                    continue
                declared = any(
                    is_mock(item, aliases)
                    for decorator in node.decorator_list
                    for item in unfold_chain(decorator, aliases)
                )
                key: Literal["declared_mock", "unmarked"] = (
                    "declared_mock" if declared else "unmarked"
                )
                result[key].append(f"{relative}:{node.name}")
    if result["errors"]:
        result["kind"] = "unknown"
    elif result["declared_mock"]:
        result["kind"] = "mixed" if result["unmarked"] else "mock"
    return result

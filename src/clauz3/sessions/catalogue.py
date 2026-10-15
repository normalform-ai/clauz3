"""Read-only AST catalogue; discovery never imports installed tool code."""

import ast
from pathlib import Path

from clauz3.decorators import (
    canonical_target,
    condition_contract,
    dotted_name,
    import_aliases,
    is_mock,
    is_trusted_effect,
    unfold_chain,
)
from clauz3.libraries.layout import project_dir


def snapshot_catalogue(cwd: Path, roots: list[Path]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"api": [], "source": []}
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                continue
            relative = path.relative_to(cwd)
            # Installed layers import from the .clauz3 root, not the project.
            installed = project_dir(cwd)
            importable = (
                path.relative_to(installed)
                if path.is_relative_to(installed)
                else relative
            )
            header = f"# {relative}"
            try:
                source = path.read_text()
                if not source.strip():
                    continue
                result["source"].append(header + "\n" + source)
                module = ast.parse(source, filename=str(relative))
            except (SyntaxError, ValueError) as exc:
                result["api"].append(
                    f"{header}\nCannot parse API: {exc}; this file is not listed. "
                    "Ask the user to repair the installed library."
                )
                continue
            aliases = import_aliases(module)
            entries = []
            for node in module.body:
                if not isinstance(node, ast.FunctionDef | ast.ClassDef):
                    continue
                if node.name.startswith("_"):
                    continue
                module_path = importable.with_suffix("")
                if path.name == "__init__.py":
                    module_path = module_path.parent
                module_name = ".".join(module_path.parts)
                if isinstance(node, ast.ClassDef):
                    if any(
                        canonical_target(dotted_name(base), aliases)
                        in {"clauz3.Row", "clauz3.row.Row"}
                        for base in node.bases
                    ):
                        fields = [
                            ast.unparse(field)
                            for field in node.body
                            if isinstance(field, ast.AnnAssign)
                        ]
                        entries.append(
                            f"Row type: from {module_name} import {node.name}\n"
                            + "\n".join(fields)
                        )
                    continue
                if not isinstance(node, ast.FunctionDef):
                    continue
                declared = [
                    item
                    for decorator in node.decorator_list
                    for item in unfold_chain(decorator, aliases)
                    if isinstance(item, ast.expr)
                ]
                decorators = {
                    canonical_target(dotted_name(d), aliases) for d in declared
                }
                if path.name != "__init__.py" and is_trusted_effect(node, aliases):
                    kind = "Effect"
                elif decorators & {"clauz3.spec.contract", "clauz3.contract"}:
                    kind = "Contract (use inside @clauz3.guarantee)"
                else:
                    continue
                signature = f"{node.name}({ast.unparse(node.args)})"
                if node.returns is not None:
                    signature += f" -> {ast.unparse(node.returns)}"
                lines = [
                    f"{kind}: {signature}",
                    f"Import: from {module_name} import {node.name}",
                ]
                if kind == "Effect" and any(is_mock(d, aliases) for d in declared):
                    lines.append(
                        "Declared mock: the library author marks this effect as "
                        "simulated. This declaration does not enforce isolation "
                        "or bypass proof and approval."
                    )
                if doc := ast.get_docstring(node):
                    lines.append(doc)
                preconditions = [
                    ast.unparse(d)
                    for d in declared
                    if condition_contract(d, aliases) == "pre"
                ]
                if preconditions:
                    lines += ["Preconditions:", *preconditions]
                entries.append("\n".join(lines))
            if entries:
                result["api"].append(header + "\n\n" + "\n\n".join(entries))
    return result

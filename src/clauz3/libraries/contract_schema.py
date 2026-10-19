"""Export trusted Python signatures and descriptions as a LinkML schema.

This build tool reads source through AST; it never imports or executes a
library. JSON is used as the LinkML serialization so export needs no additional
dependencies. LinkML's optional generators can consume this file directly.
"""

import argparse
import ast
import inspect
import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from clauz3.contract_metadata import (
    RENDERER_VERSION,
    element_kind,
    parameter_types,
    validate_template,
)
from clauz3.decorators import (
    canonical_target,
    dotted_name,
    import_aliases,
    is_trusted_effect,
)

NAMESPACE = "https://normalform-ai.github.io/clauz3/schema/contracts/v1/"
_RANGES = {
    "str": "string",
    "int": "integer",
    "float": "double",
    "bool": "boolean",
    "FilePath": "FilePath",
    "DirectoryPath": "DirectoryPath",
}


def _signature(node: ast.FunctionDef) -> inspect.Signature:
    if node.args.vararg or node.args.kwarg:
        raise ValueError(f"cannot export variadic parameters: {node.name}")
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults: list[ast.expr | None] = [None] * (
        len(positional) - len(node.args.defaults)
    ) + list(node.args.defaults)
    arguments = [*positional, *node.args.kwonlyargs]
    defaults += node.args.kw_defaults
    parameters = []
    for index, (arg, default) in enumerate(zip(arguments, defaults, strict=True)):
        annotation: object = inspect.Parameter.empty
        if isinstance(arg.annotation, ast.Name):
            annotation = arg.annotation.id
        elif isinstance(arg.annotation, ast.Constant):
            annotation = arg.annotation.value
        elif isinstance(arg.annotation, ast.Subscript):
            annotation = ast.unparse(arg.annotation)
        kind = (
            inspect.Parameter.POSITIONAL_ONLY
            if index < len(node.args.posonlyargs)
            else inspect.Parameter.POSITIONAL_OR_KEYWORD
            if index < len(positional)
            else inspect.Parameter.KEYWORD_ONLY
        )
        parameters.append(
            inspect.Parameter(
                arg.arg,
                kind,
                annotation=annotation,
                default=ast.literal_eval(default)
                if default
                else inspect.Parameter.empty,
            )
        )
    return inspect.Signature(parameters)


def schema_for_sources(sources: Mapping[str, Path]) -> dict[str, object]:
    """Extract the parameter catalog from explicit module/source pairs."""
    classes: dict[str, object] = {
        "Contract": {
            "abstract": True,
            "description": "A constraint on the reachable effects of a program.",
        },
        "EffectCall": {
            "abstract": True,
            "description": "An individual trusted call event.",
        },
    }
    for module_name, path in sorted(sources.items()):
        source_bytes = path.read_bytes()
        source = source_bytes.decode("utf-8")
        module = ast.parse(source, filename=str(path))
        aliases = import_aliases(module)
        for node in module.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            declarations = [
                d
                for d in node.decorator_list
                if canonical_target(dotted_name(d), aliases)
                in {"clauz3.spec.contract", "clauz3.contract"}
            ]
            parent = "Contract" if declarations else "EffectCall"
            if not declarations and not is_trusted_effect(node, aliases):
                continue
            symbol = f"{module_name}.{node.name}"
            try:
                signature = _signature(node)
                kinds = parameter_types(signature)
            except ValueError as exc:
                raise ValueError(f"cannot export {symbol}: {exc}") from exc
            # Keep namespaces visible in human-readable class names. class_uri
            # uses the full symbol; a collision in the short name is an error.
            library = module_name.split(".")[-3]
            name = "".join(
                part.capitalize() for part in f"{library}_{node.name}".split("_")
            )
            if name in classes:
                raise ValueError(f"duplicate schema class: {name}")
            annotations: dict[str, object] = {
                "python_symbol": symbol,
                "source_sha256": sha256(source_bytes).hexdigest(),
            }
            if declarations and isinstance(declarations[0], ast.Call):
                for keyword in declarations[0].keywords:
                    if keyword.arg == "description":
                        template = ast.literal_eval(keyword.value)
                        validate_template(template, kinds)
                        annotations["cnl_template"] = template
                        annotations["renderer_version"] = RENDERER_VERSION
            attributes = {}
            for parameter_name, kind in kinds.items():
                parameter = signature.parameters[parameter_name]
                has_default = parameter.default is not parameter.empty
                slot_annotations = {"python_parameter_kind": parameter.kind.name}
                if has_default:
                    slot_annotations["python_default"] = json.dumps(parameter.default)
                element = element_kind(kind)
                attributes[parameter_name] = {
                    "slot_uri": f"cz:{symbol}.{parameter_name}",
                    "range": _RANGES[element or kind],
                    "required": not has_default,
                    "description": f"The {parameter_name} argument to {symbol}.",
                    "annotations": slot_annotations,
                }
                if element is not None:
                    attributes[parameter_name]["multivalued"] = True
            classes[name] = {
                "is_a": parent,
                "class_uri": f"cz:{symbol}",
                "description": ast.get_docstring(node) or symbol,
                "attributes": attributes,
                "annotations": annotations,
            }
    return {
        "id": NAMESPACE.rstrip("/"),
        "name": "clauz3_contracts",
        "version": str(RENDERER_VERSION),
        "title": "clauz3 filesystem contract catalog",
        "description": (
            "Generated from trusted Python definitions. Classes describe contract "
            "invocations and effect calls; exports do not establish program safety."
        ),
        "prefixes": {"linkml": "https://w3id.org/linkml/", "cz": NAMESPACE},
        "imports": ["linkml:types"],
        "default_prefix": "cz",
        "default_range": "string",
        "types": {
            "FilePath": {
                "typeof": "string",
                "description": "An exact lexical file path.",
            },
            "DirectoryPath": {
                "typeof": "string",
                "description": "A lexical directory boundary.",
            },
        },
        "classes": classes,
    }


def bundled_schema() -> dict[str, object]:
    stdlib = Path(__file__).resolve().parents[1] / "stdlib"
    return schema_for_sources(
        {
            f"tools.{library}.trusted.{module}": stdlib
            / library
            / "tools"
            / library
            / "trusted"
            / f"{module}.py"
            for library in ("filesystem",)
            for module in ("contracts", "effects")
        }
    )


def serialize_bundled_schema() -> str:
    return json.dumps(bundled_schema(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = serialize_bundled_schema()
    if args.check:
        if (
            not args.output.exists()
            or args.output.read_text(encoding="utf-8") != content
        ):
            parser.exit(1, "Contract schema is stale; run just contract-schema.\n")
    else:
        args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()

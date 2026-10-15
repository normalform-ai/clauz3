"""Run with isolated LinkML dependencies; see the accompanying design note.

This exercises schema exports and a deliberately small CNL renderer. It does
not prove a program, discover trusted functions, or authorize an execution.
"""

import argparse
import json
from pathlib import Path
from string import Formatter

from jsonschema import ValidationError
from jsonschema.validators import validator_for
from linkml.generators.jsonschemagen import JsonSchemaGenerator
from linkml.generators.owlgen import OwlSchemaGenerator
from linkml.generators.shaclgen import ShaclGenerator
from linkml_runtime.utils.schemaview import SchemaView
from rdflib import RDF, RDFS, Graph, Namespace

SCHEMA = Path(__file__).resolve().parents[2] / "reference/contracts.linkml.json"
CASES = (
    ("FilesystemReadOnly", {}),
    ("FilesystemOnlyReadUnder", {"root": "test.md"}),
    ("EditorNoEdits", {}),
)


def describe(view: SchemaView, name: str, values: dict[str, object]) -> str:
    """Only named-slot substitution; no expressions, lookup paths or formatting."""
    schema = json.loads(JsonSchemaGenerator(SCHEMA, top_class=name).serialize())
    validator = validator_for(schema)
    validator.check_schema(schema)
    validator(schema).validate(values)
    template = view.get_class(name).annotations["cnl_template"].value
    slots = set(view.class_slots(name))
    parts = []
    for literal, field, spec, conversion in Formatter().parse(template):
        parts.append(literal)
        if field is not None:
            if field not in slots or spec or conversion:
                raise ValueError(f"unsupported template field: {field}")
            # Quoting preserves control characters and distinguishes a literal
            # parameter from the surrounding sentence. HTML escaping belongs to
            # the UI after this plain-text rendering step.
            parts.append(json.dumps(values[field], ensure_ascii=True, sort_keys=True))
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    view = SchemaView(SCHEMA)
    descriptions = [describe(view, name, values) for name, values in CASES]
    expected = [
        "No calls to filesystem.write_file are permitted.",
        "Every filesystem.read_file or filesystem.read_lines "
        'path must start with "test.md".',
        "No calls to editor.edit_file or editor.append_file are permitted.",
    ]
    assert descriptions == expected
    for invalid in ({}, {"root": 4}, {"root": "test.md", "unknown": True}):
        try:
            describe(view, "FilesystemOnlyReadUnder", invalid)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"invalid parameters accepted: {invalid}")
    assert "\\n" in describe(view, "FilesystemOnlyReadUnder", {"root": "a\nb"})

    owl = OwlSchemaGenerator(
        SCHEMA,
        use_native_uris=False,
        skip_abstract_class_as_unionof_subclasses=True,
        skip_vacuous_min_zero_cardinality_axioms=True,
        skip_vacuous_local_range_axioms=True,
        consolidate_cardinality_axioms=True,
    ).serialize()
    shacl = ShaclGenerator(SCHEMA).serialize()
    owl_graph = Graph().parse(data=owl, format="turtle")
    shacl_graph = Graph().parse(data=shacl, format="turtle")
    cz = Namespace("https://normalform-ai.github.io/clauz3/schema/contracts/v1/")
    sh = Namespace("http://www.w3.org/ns/shacl#")
    contract = cz["tools.filesystem.trusted.contracts.only_read_under"]
    assert (contract, RDFS.subClassOf, cz.Contract) in owl_graph
    assert (None, sh.targetClass, contract) in shacl_graph
    assert (None, RDF.type, sh.NodeShape) in shacl_graph
    (args.output / "contracts.owl.ttl").write_text(owl, encoding="utf-8")
    (args.output / "contracts.shacl.ttl").write_text(shacl, encoding="utf-8")
    (args.output / "descriptions.txt").write_text(
        "\n".join(descriptions) + "\n", encoding="utf-8"
    )
    for name, _ in CASES:
        (args.output / f"{name}.schema.json").write_text(
            JsonSchemaGenerator(SCHEMA, top_class=name).serialize(), encoding="utf-8"
        )
    print("\n".join(descriptions))
    print(f"Validated parameters and exported OWL, SHACL, JSON Schema to {args.output}")


if __name__ == "__main__":
    main()

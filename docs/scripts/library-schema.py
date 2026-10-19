"""Generate the runtime validator from the LinkML source (isolated LinkML env)."""

import argparse
import json
from hashlib import sha256
from pathlib import Path

from linkml.generators.jsonschemagen import JsonSchemaGenerator

root = Path(__file__).resolve().parents[2]
source = root / "src/clauz3/schemas/library.linkml.yaml"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
for name, top_class in (("library", "LibraryManifest"), ("package", "PackageManifest")):
    destination = source.with_name(f"{name}.schema.json")
    data = json.loads(JsonSchemaGenerator(source, top_class=top_class).serialize())
    data["$comment"] = (
        "LinkML source SHA256: " + sha256(source.read_bytes()).hexdigest()
    )
    rendered = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if args.check:
        if destination.read_text() != rendered:
            raise SystemExit(f"{name} JSON Schema is stale; run just library-schema")
    else:
        destination.write_text(rendered)

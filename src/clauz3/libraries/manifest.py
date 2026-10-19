"""Read library-owned metadata without importing code or running setup steps."""

import json
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema import Draft202012Validator, ValidationError
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken

MANIFEST = "clauz3.yaml"
PACKAGE_SNAPSHOT = ".clauz3-package.yaml"
MAX_METADATA_BYTES = 128_000


class ManifestError(ValueError):
    """Invalid declarative library metadata."""


class _Loader(yaml.SafeLoader):
    pass


def _mapping(
    loader: _Loader, node: MappingNode, deep: bool = False
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ManifestError("metadata keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_yaml(text: str, label: str) -> dict[str, Any]:
    """Load a bounded YAML mapping; reject aliases, duplicate keys and object tags."""
    if len(text.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ManifestError(f"{label}: metadata is too large")
    try:
        if any(isinstance(token, AliasToken) for token in yaml.scan(text)):
            raise ManifestError("YAML aliases are not supported")
        data = yaml.load(text, Loader=_Loader)
        if not isinstance(data, dict):
            raise ManifestError("expected a YAML mapping")
        return cast(dict[str, Any], data)
    except (yaml.YAMLError, ValueError, RecursionError) as exc:
        raise ManifestError(f"{label}: {exc}") from exc


@lru_cache(maxsize=2)
def _validator(kind: str) -> Draft202012Validator:
    schema = json.loads((files("clauz3") / f"schemas/{kind}.schema.json").read_text())
    return Draft202012Validator(schema)


def _parse(text: str, *, label: str, kind: str) -> dict[str, Any]:
    data = load_yaml(text, label)
    try:
        _validator(kind).validate(data)
    except (ValidationError, RecursionError) as exc:
        detail = exc.message if isinstance(exc, ValidationError) else str(exc)
        raise ManifestError(f"{label}: {detail}") from exc
    data = cast(dict[str, Any], _without_nulls(data))
    setup = data.get("requirements", {}).get("setup", [])
    ids = [step["id"] for step in setup]
    if len(set(ids)) != len(ids):
        raise ManifestError(f"{label}: setup IDs must be unique")
    names = [tool["name"] for tool in data.get("requirements", {}).get("tools", [])]
    if len(set(names)) != len(names):
        raise ManifestError(f"{label}: tool names must be unique")
    return data


def parse_manifest(text: str, *, label: str, domain: str) -> dict[str, Any]:
    data = _parse(text, label=label, kind="library")
    if data["name"] != domain:
        raise ManifestError(f"{label}: name must match domain {domain!r}")
    return data


def parse_package(text: str, *, label: str) -> dict[str, Any]:
    data = _parse(text, label=label, kind="package")
    if any(s.get("applies_to") for s in data.get("requirements", {}).get("setup", [])):
        raise ManifestError(
            f"{label}: effect-specific applies_to belongs in a domain manifest"
        )
    return data


def _without_nulls(value: Any) -> Any:
    # LinkML optional slots accept null; normalize them to absent slots.
    if isinstance(value, dict):
        return {k: _without_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_without_nulls(v) for v in value]
    return value


def _read(path: Path) -> str | None:
    if path.is_symlink():
        raise ManifestError(f"{path}: manifest must not be a symbolic link")
    if not path.exists():
        return None
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise ManifestError(f"{path}: metadata is too large")
    return path.read_text()


def read_package(path: Path) -> dict[str, Any] | None:
    text = _read(path)
    return parse_package(text, label=str(path)) if text is not None else None


def merge_package(
    manifest: dict[str, Any] | None, package: dict[str, Any] | None, *, label: str
) -> dict[str, Any] | None:
    """Combine shared prerequisites without overriding domain-owned descriptions."""
    if package is None:
        return manifest
    if manifest is None:
        raise ManifestError(f"{label}: a package requires a manifest for every domain")
    result = deepcopy(manifest)
    shared = package.get("requirements", {})
    own = manifest.get("requirements", {})
    requirements: dict[str, Any] = {}
    for field, key in (("python", None), ("tools", "name"), ("setup", "id")):
        entries: dict[str, Any] = {}
        for entry in [*shared.get(field, []), *own.get(field, [])]:
            identity = entry[key] if key else entry
            if identity in entries and entries[identity] != entry:
                raise ManifestError(
                    f"{label}: conflicting {field} requirement {identity!r}"
                )
            entries[identity] = deepcopy(entry)
        if entries:
            requirements[field] = list(entries.values())
    if requirements:
        result["requirements"] = requirements
    return result


def read_manifest(directory: Path, domain: str) -> dict[str, Any] | None:
    """Read a domain and its installed, revision-specific package snapshot."""
    path = directory / MANIFEST
    text = _read(path)
    manifest = (
        parse_manifest(text, label=str(path), domain=domain)
        if text is not None
        else None
    )
    return merge_package(
        manifest, read_package(directory / PACKAGE_SNAPSHOT), label=str(path)
    )


def source_package(tools: Path, domain: str) -> dict[str, Any] | None:
    """Read the source-root package or retain a copied domain's package snapshot."""
    package = read_package(tools.parent / MANIFEST)
    snapshot = read_package(tools / domain / PACKAGE_SNAPSHOT)
    if package is not None and snapshot is not None and package != snapshot:
        raise ManifestError(
            f"{tools / domain}: package snapshot conflicts with source root"
        )
    return package if package is not None else snapshot


def read_source_manifest(tools: Path, domain: str) -> dict[str, Any] | None:
    return merge_package(
        read_manifest(tools / domain, domain),
        source_package(tools, domain),
        label=str(tools / domain),
    )


def requirement_summaries(manifest: dict[str, Any]) -> tuple[str, ...]:
    """Compact CLI and installation-review text; structured data stays available."""
    requirements = manifest.get("requirements", {})
    return tuple(
        [f"Python package {package}" for package in requirements.get("python", [])]
        + [tool["description"] for tool in requirements.get("tools", [])]
        + [step["title"] for step in requirements.get("setup", [])]
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    paths = args.paths or sorted(
        Path(str(files("clauz3") / "stdlib")).glob("*/tools/**/clauz3.yaml")
    )
    schema_root = files("clauz3") / "schemas"
    digest = sha256((schema_root / "library.linkml.yaml").read_bytes()).hexdigest()
    for name in ("library", "package"):
        schema = json.loads((schema_root / f"{name}.schema.json").read_text())
        if schema.get("$comment") != "LinkML source SHA256: " + digest:
            raise ManifestError(f"{name} JSON Schema is stale; run just library-schema")
    for path in paths:
        if not path.is_file():
            raise ManifestError(f"manifest not found: {path}")
        if load_yaml(_read(path) or "", str(path)).get("kind") == "package":
            read_package(path)
            continue
        tools_root = next((p for p in path.parents if p.name == "tools"), None)
        domain = (
            str(path.parent.relative_to(tools_root)) if tools_root else path.parent.name
        )
        if tools_root:
            read_source_manifest(tools_root, domain)
        else:
            read_manifest(path.parent, domain)
    print(f"Validated {len(paths)} library manifests")


if __name__ == "__main__":
    main()

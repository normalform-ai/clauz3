"""Read-only library discovery. Catalogue lookups never import or clone code."""

import json
import os
import re
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

from clauz3.libraries.layout import tools_dirs, trusted_dirs
from clauz3.libraries.manifest import (
    ManifestError,
    load_yaml,
    parse_manifest,
    read_manifest,
    read_source_manifest,
    requirement_summaries,
    source_package,
)

#: Libraries whose tools a newer bundled library now provides. Their copies
#: define effects with the same names, so they cannot be proved beside it.
SUPERSEDED: dict[str, tuple[str, ...]] = {
    "filesystem": ("coreutils", "editor", "find", "grep", "text"),
    "google_workspace/gmail": ("email",),
}


def superseded_message(library: str, stale: list[str]) -> str:
    """How to migrate a project that still holds superseded libraries.

    >>> superseded_message("filesystem", ["find", "grep"])[:58]
    'stdlib:filesystem now provides the tools of find, grep, wh'
    """
    bundle = library.split("/")[0]
    return (
        f"stdlib:{bundle} now provides the tools of {', '.join(stale)}, whose "
        "copies define effects with the same names. Remove those libraries "
        "(from the Libraries view, or delete them under .clauz3/tools/), then "
        f"install stdlib:{bundle} again. Imports or contracts may have changed; "
        f"see the stdlib:{bundle} README."
    )


@dataclass(frozen=True)
class Library:
    source: str
    description: str
    domains: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    url: str = ""
    visibility: str = "bundled"
    origin: str = ""
    title: str = ""
    manifests: tuple[dict[str, Any], ...] = ()


class LibraryRecord(TypedDict):
    source: str
    description: str
    domains: list[str]
    dependencies: list[str]
    requirements: list[str]
    url: str
    visibility: str
    installed: bool
    install_command: str
    origin: str
    title: str
    manifests: list[dict[str, Any]]


def global_library_root() -> Path:
    """User-wide storage, deliberately absent from runtime import/trusted roots."""
    if directory := os.environ.get("CLAUZ3_HOME"):
        return Path(directory).expanduser().resolve()
    data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return data.expanduser().resolve() / "clauz3"


def global_domain(source: str) -> str | None:
    if not source.startswith("global:"):
        return None
    domain = source.removeprefix("global:")
    if not re.fullmatch(r"[A-Za-z_][\w-]*(/[A-Za-z_][\w-]*)*", domain):
        raise ValueError(f"invalid global library name: {domain!r}")
    return domain


def library_from_manifests(
    source: str,
    domains: dict[str, dict[str, Any] | None],
    *,
    visibility: str,
    origin: str = "",
    title: str = "",
) -> Library:
    manifests = tuple(value for value in domains.values() if value is not None)
    return Library(
        source=source,
        description="\n".join(m["summary"] for m in manifests)
        or "Installed library; no description manifest provided.",
        domains=tuple(domains),
        dependencies=tuple(
            dict.fromkeys(d for m in manifests for d in m.get("dependencies", []))
        ),
        requirements=tuple(
            dict.fromkeys(r for m in manifests for r in requirement_summaries(m))
        ),
        visibility=visibility,
        origin=origin or source,
        title=title or ", ".join(m["title"] for m in manifests) or ", ".join(domains),
        manifests=manifests,
    )


def _catalog_cache(source: str) -> Path:
    return (
        global_library_root()
        / "catalog"
        / f"{sha256(source.encode()).hexdigest()}.json"
    )


def cache_library(
    source: str, domain_root: Path, domains: list[str], revision: str
) -> None:
    """Cache metadata during explicit install/review, never during listing."""
    data = {
        "source": source,
        "revision": revision,
        "domains": {d: read_source_manifest(domain_root, d) for d in domains},
    }
    path = _catalog_cache(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def catalog() -> list[Library]:
    registry = load_yaml(
        (files("clauz3") / "library_registry.yaml").read_text(), "library registry"
    )
    sources = registry.get("sources")
    if (
        set(registry) != {"sources"}
        or not isinstance(sources, list)
        or not all(isinstance(source, str) for source in sources)
        or len(set(sources)) != len(sources)
    ):
        raise ManifestError("library registry must contain a unique list of sources")
    result = []
    for source in sources:
        if source.startswith("stdlib:"):
            bundle = source.removeprefix("stdlib:")
            with as_file(files("clauz3") / f"stdlib/{bundle}") as root:
                tools = root / "tools"
                domain_names = [
                    trusted.parent.relative_to(tools).as_posix()
                    for trusted in trusted_dirs(tools)
                ]
                manifests = {d: read_source_manifest(tools, d) for d in domain_names}
                package = (
                    source_package(tools, domain_names[0]) if domain_names else None
                )
            result.append(
                library_from_manifests(
                    source,
                    manifests,
                    visibility="bundled",
                    title=package["title"] if package else "",
                )
            )
        elif _catalog_cache(source).is_file():
            path = _catalog_cache(source)
            data = json.loads(path.read_text())
            domains = {
                domain: parse_manifest(
                    json.dumps(value), label=str(path), domain=domain
                )
                if value is not None
                else None
                for domain, value in data["domains"].items()
            }
            result.append(
                library_from_manifests(source, domains, visibility="repository")
            )
        else:
            result.append(
                Library(
                    source=source,
                    domains=(),
                    visibility="repository",
                    title=source,
                    description=(
                        "Review installation to load this library's "
                        "description and setup requirements."
                    ),
                )
            )
    return result


def installed_libraries(project: Path) -> list[Library]:
    """Installed manifests describe the selected copy, even without a registry entry."""
    grouped: dict[str, dict[str, dict[str, Any] | None]] = {}
    titles: dict[str, str] = {}
    seen: set[str] = set()
    for root in tools_dirs(project):
        for domain in sorted(_domains(root)):
            if domain in seen:
                continue
            seen.add(domain)
            directory = root / domain
            manifest = read_source_manifest(root, domain)
            origin_path = directory / ".clauz3-origin.json"
            if manifest is None and not origin_path.is_file():
                continue  # Preserve legacy registry discovery for older copies.
            source = f"project:{domain}"
            if origin_path.is_file():
                origin = json.loads(origin_path.read_text())
                if not isinstance(origin, dict) or not isinstance(
                    origin.get("source"), str
                ):
                    raise ManifestError(f"invalid library origin: {origin_path}")
                source = origin["source"]
            grouped.setdefault(source, {})[domain] = manifest
            package = source_package(root, domain)
            if package is not None:
                titles[source] = package["title"]
    return [
        library_from_manifests(
            source, domains, visibility="project", title=titles.get(source, "")
        )
        for source, domains in grouped.items()
    ]


def global_catalog() -> list[Library]:
    # Do not read or execute library source to list user-wide installations.
    # Sidecar metadata is written by the operator's install command.
    result = []
    for domain in sorted(_domains(global_library_root() / "tools")):
        metadata = global_library_root() / "metadata" / domain / "library.json"
        data = json.loads(metadata.read_text()) if metadata.is_file() else {}
        if not isinstance(data, dict):
            raise ValueError(f"invalid library metadata: {metadata}")
        for key in ("description", "url", "origin"):
            if key in data and not isinstance(data[key], str):
                raise ValueError(f"invalid {key} in {metadata}")
        for key in ("dependencies", "requirements"):
            if key in data and (
                not isinstance(data[key], list)
                or not all(isinstance(item, str) for item in data[key])
            ):
                raise ValueError(f"invalid {key} in {metadata}")
        manifest = read_manifest(global_library_root() / "tools" / domain, domain)
        if manifest is not None:
            library = library_from_manifests(
                f"global:{domain}",
                {domain: manifest},
                visibility="global",
                origin=data.get("origin", ""),
            )
            result.append(
                replace(
                    library,
                    dependencies=tuple(
                        "global:" + dep.removeprefix("stdlib:")
                        if dep.startswith("stdlib:")
                        else dep
                        for dep in library.dependencies
                    ),
                )
            )
            continue
        result.append(
            Library(
                source=f"global:{domain}",
                description=data.get("description", "User-wide trusted library."),
                domains=(domain,),
                dependencies=tuple(data.get("dependencies", [])),
                requirements=tuple(data.get("requirements", [])),
                url=data.get("url", ""),
                visibility="global",
                origin=data.get("origin", ""),
            )
        )
    return result


def installed_domains(project: Path) -> set[str]:
    """Domains a project provides, installed under ``.clauz3/`` or authored."""
    return {domain for tools in tools_dirs(project) for domain in _domains(tools)}


def _domains(tools: Path) -> set[str]:
    return {
        str(path.parent.relative_to(tools))
        for path in tools.glob("**/trusted")
        if path.is_dir()
    }


def library_records(project: Path, query: str = "") -> list[LibraryRecord]:
    """Search descriptions, names and metadata; report domain presence only."""
    installed = installed_domains(project)
    result: list[LibraryRecord] = []
    local = installed_libraries(project)
    described = {domain for library in local for domain in library.domains}
    available = [lib for lib in catalog() if not (set(lib.domains) & described)]
    choices = {lib.source: lib for lib in [*available, *global_catalog(), *local]}
    for library in choices.values():
        text = json.dumps(asdict(library)).casefold()
        if not all(term in text for term in query.casefold().split()):
            continue
        record: LibraryRecord = {
            "source": library.source,
            "description": library.description,
            "domains": list(library.domains),
            "dependencies": list(library.dependencies),
            "requirements": list(library.requirements),
            "url": library.url,
            "visibility": library.visibility,
            "installed": bool(library.domains)
            and all(domain in installed for domain in library.domains),
            "install_command": f"clauz3 install {library.source}",
            "origin": library.origin or library.source,
            "title": library.title or ", ".join(library.domains) or library.source,
            "manifests": list(library.manifests),
        }
        result.append(record)
    return result


def format_libraries(records: list[LibraryRecord]) -> str:
    if not records:
        return "No libraries match. Try clauz3 libraries without a query."
    lines = []
    for record in records:
        status = "project domains present" if record["installed"] else "not installed"
        lines.append(f"{record['source']} [{record['visibility']}; {status}]")
        lines.append(f"  {record['description']}")
        if record["dependencies"]:
            lines.append(f"  Library dependencies: {', '.join(record['dependencies'])}")
        if record["requirements"]:
            lines.append(f"  Runtime requirements: {', '.join(record['requirements'])}")
        if record["url"]:
            lines.append(f"  Source: {record['url']}")
        if record["origin"] != record["source"]:
            lines.append(f"  Stored from: {record['origin']}")
        lines.append(f"  Install: {record['install_command']}")
    return "\n".join(lines)

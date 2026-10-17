"""Read-only library discovery. Catalogue lookups never import or clone code."""

import json
import os
import re
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path
from typing import TypedDict

from clauz3.libraries.layout import tools_dirs

#: Libraries whose tools a newer bundled library now provides. Their copies
#: define effects with the same names, so they cannot be proved beside it.
SUPERSEDED: dict[str, tuple[str, ...]] = {
    "filesystem": ("coreutils", "editor", "find", "grep", "text"),
}


def superseded_message(library: str, stale: list[str]) -> str:
    """How to migrate a project that still holds superseded libraries.

    >>> superseded_message("filesystem", ["find", "grep"])[:58]
    'stdlib:filesystem now provides the tools of find, grep, wh'
    """
    return (
        f"stdlib:{library} now provides the tools of {', '.join(stale)}, whose "
        "copies define effects with the same names. Remove those libraries "
        "(from the Libraries view, or delete them under .clauz3/tools/), then "
        f"install stdlib:{library} again. Some contracts were renamed; see the "
        f"stdlib:{library} README."
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


def catalog() -> list[Library]:
    data = json.loads((files("clauz3") / "library_catalog.json").read_text())
    return [
        Library(
            source=item["source"],
            description=item["description"],
            domains=tuple(item["domains"]),
            dependencies=tuple(item["dependencies"]),
            requirements=tuple(item["requirements"]),
            url=item.get("url", ""),
            visibility=item.get("visibility", "bundled"),
        )
        for item in data
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
    for library in [*catalog(), *global_catalog()]:
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
            "installed": all(domain in installed for domain in library.domains),
            "install_command": f"clauz3 install {library.source}",
            "origin": library.origin or library.source,
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

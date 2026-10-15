"""Built-in inert documentation and recipes for matching installed libraries."""

from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from clauz3.libraries.layout import domain_path
from clauz3.sessions.skills import SkillDocument

AUTHORING_ID = "builtin:clauz3-authoring"
RECIPES = {
    "read-file": ("Read one UTF-8 file", ("filesystem",)),
    "count-lines": ("Count logical lines, including blank lines", ("filesystem",)),
    "write-and-read": ("Write a file and read it back", ("filesystem",)),
    "grep-named-files": ("Search two named files", ("filesystem", "grep")),
    "list-files": ("List all file names", ("filesystem", "find")),
    "grep-directory": (
        "Search UTF-8 files in a directory, reporting skipped paths",
        ("filesystem", "find", "grep"),
    ),
}


def _bundled_sources(root: Traversable, prefix: str = "") -> dict[str, bytes]:
    result = {}
    for item in root.iterdir():
        name = prefix + item.name
        if item.is_dir():
            result.update(_bundled_sources(item, name + "/"))
        elif item.name.endswith(".py"):
            result[name] = item.read_bytes()
    return result


def _matches(cwd: Path, roots: list[Path], domain: str) -> bool:
    root = domain_path(cwd, domain) / "trusted"
    if root.resolve() not in {path.resolve() for path in roots}:
        return False
    reference = files("clauz3") / "stdlib" / domain / "tools" / domain / "trusted"
    expected = _bundled_sources(reference)
    actual = {}
    for path in root.rglob("*.py"):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            return False
        actual[path.relative_to(root).as_posix()] = path.read_bytes()
    return actual == expected


def builtin_documents(cwd: Path, roots: list[Path]) -> tuple[SkillDocument, ...]:
    resources = files("clauz3.sessions") / "documents"
    documents = [
        SkillDocument(
            id=AUTHORING_ID,
            name="clauz3-authoring",
            preview=(
                "Read before your first run: supported Python, guarantees, "
                "discovery, loop limitations, and proof/approval status."
            ),
            content=(resources / "authoring.md").read_text(),
        )
    ]
    compatible = {
        domain: _matches(cwd, roots, domain)
        for domain in {d for _, domains in RECIPES.values() for d in domains}
    }
    for name, (description, domains) in RECIPES.items():
        if all(compatible[domain] for domain in domains):
            documents.append(
                SkillDocument(
                    id=f"builtin:recipe:{name}",
                    name=name,
                    preview=f"{description}. Tested with bundled {', '.join(domains)}.",
                    content=(resources / f"{name}.md").read_text(),
                )
            )
    return tuple(documents)

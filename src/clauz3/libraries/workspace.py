"""Operator-reviewed library changes, backed by the existing CLI installer."""

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from clauz3.libraries.discovery import (
    SUPERSEDED,
    Library,
    catalog,
    global_catalog,
    installed_domains,
    superseded_message,
)
from clauz3.libraries.install import install_library
from clauz3.libraries.layout import domain_path, ensure_project_dir
from clauz3.libraries.mock_metadata import MockDeclarations, mock_declarations
from clauz3.sessions.catalogue import snapshot_catalogue


def safe_path(project: Path, domain: str) -> Path:
    if not re.fullmatch(r"[A-Za-z_][\w-]*(/[A-Za-z_][\w-]*)*", domain):
        raise ValueError("Choose an installed library from the list")
    # An existing copy is managed where it is (installed under .clauz3/ or a
    # legacy top-level tools/); a new one goes under .clauz3/tools/.
    path = domain_path(project, domain)
    for component in (path, *path.parents):
        if component == project:
            break
        if component.is_symlink():
            raise ValueError("Symbolic links cannot be managed as project libraries")
    return path


def tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return "missing"
    for file in sorted(path.rglob("*")):
        if file.is_symlink():
            raise ValueError(
                "Library trees containing symbolic links cannot be changed"
            )
        if "__pycache__" in file.parts or file.suffix == ".pyc":
            continue
        if file.is_file():
            digest.update(str(file.relative_to(path)).encode())
            digest.update(b"\0" + file.read_bytes() + b"\0")
    return digest.hexdigest()


def overlaps(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


class WorkspaceLibraries:
    def __init__(self, project: Path, storage: Path, initial: list[str]) -> None:
        self.project = project.resolve()
        self.storage = storage
        storage.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.preferences = storage / "libraries.json"
        self.selected = initial.copy()
        if self.preferences.exists():
            data = json.loads(self.preferences.read_text())
            if isinstance(data, list) and all(isinstance(d, str) for d in data):
                self.selected = data
        self.busy = False
        self.review: dict[str, Any] | None = None
        self.staging: tempfile.TemporaryDirectory[str] | None = None

    def sources(self) -> dict[str, Library]:
        return {lib.source: lib for lib in [*catalog(), *global_catalog()]}

    def installed(self) -> list[str]:
        return sorted(installed_domains(self.project))

    def roots(self, domains: list[str]) -> list[Path]:
        roots = []
        for domain in sorted(set(domains)):
            root = safe_path(self.project, domain) / "trusted"
            if not root.is_dir():
                raise ValueError(
                    f"Library {domain} is not installed. "
                    "Choose libraries for a new conversation."
                )
            if root.is_symlink():
                raise ValueError(
                    "Symbolic links cannot be selected as trusted libraries"
                )
            tree_hash(root.parent)
            roots.append(root)
        return roots

    def fingerprint(self, domains: list[str]) -> str:
        entries = {}
        for root in self.roots(domains):
            entries[str(root)] = tree_hash(root.parent)
            for parent in root.parents:
                if parent == self.project:
                    break
                initializer = parent / "__init__.py"
                if initializer.is_symlink():
                    raise ValueError("Symbolic package initializers cannot be selected")
                if initializer.is_file():
                    entries[str(initializer)] = hashlib.sha256(
                        initializer.read_bytes()
                    ).hexdigest()
        return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()

    def snapshot(self) -> dict[str, Any]:
        installed = self.installed()
        declarations: dict[str, MockDeclarations] = {}
        for domain in installed:
            try:
                root = safe_path(self.project, domain) / "trusted"
            except ValueError as exc:
                declarations[domain] = {
                    "kind": "unknown",
                    "declared_mock": [],
                    "unmarked": [],
                    "errors": [str(exc)],
                }
            else:
                declarations[domain] = mock_declarations(self.project, [root])
        return {
            "catalog": [
                {**asdict(lib), "installed": all(d in installed for d in lib.domains)}
                for lib in self.sources().values()
            ],
            "installed": installed,
            "mockDeclarations": declarations,
            "selected": self.selected,
            "busy": self.busy,
        }

    def dependencies(self, domains: list[str]) -> list[str]:
        chosen = set(domains)
        sources = self.sources()
        # Domain presence alone does not prove provenance. Conservatively include
        # declared dependencies for any catalogue entry matching a selected domain.
        while True:
            previous = chosen.copy()
            for library in sources.values():
                if any(d in chosen for d in library.domains):
                    for source in library.dependencies:
                        if source not in sources:
                            raise ValueError(f"Unknown dependency: {source}")
                        chosen.update(sources[source].domains)
            if chosen == previous:
                return sorted(chosen)

    def select(self, domains: list[str]) -> list[str]:
        selected = self.dependencies(domains)
        self.roots(selected)
        temporary = self.preferences.with_suffix(".tmp")
        temporary.write_text(json.dumps(selected))
        temporary.chmod(0o600)
        temporary.replace(self.preferences)
        self.selected = selected
        return selected

    def usable(self, domains: list[str]) -> list[str]:
        """The installed part of ``domains``, with installed dependencies.

        A domain whose dependencies are not all installed is dropped too, so
        the result never names a missing library.
        """
        installed = set(self.installed())
        kept = [d for d in domains if d in installed]
        while True:
            closed = self.dependencies(kept)
            missing = set(closed) - installed
            if not missing:
                return closed
            kept = [d for d in kept if not missing & set(self.dependencies([d]))]

    def prune(self) -> list[str]:
        """Drop defaults that can no longer be used; return what was dropped.

        A library can disappear outside the Libraries page (deleted by hand,
        or a checkout change), and a default naming it would otherwise break
        every new conversation.
        """
        usable = self.usable(self.selected)
        dropped = sorted(set(self.selected) - set(usable))
        if dropped:
            self.select(usable)
        return dropped

    def inspect(self, source: str) -> dict[str, Any]:
        library = self.sources().get(source)
        if library is None:
            raise ValueError("Choose a library from the catalogue")
        domains = [d for d in library.domains if d in self.installed()]
        return {
            "source": source,
            "api": snapshot_catalogue(self.project, self.roots(domains))["api"],
            "mockDeclarations": mock_declarations(self.project, self.roots(domains)),
        }

    def discard(self) -> None:
        self.review = None
        if self.staging:
            self.staging.cleanup()
            self.staging = None

    def prepare(self, action: str, source: str, domain: str) -> dict[str, Any]:
        self.discard()
        self.staging = tempfile.TemporaryDirectory(
            prefix="install-", dir=ensure_project_dir(self.project)
        )
        stage = Path(self.staging.name)
        requirements: list[str] = []
        if action == "remove":
            if domain not in self.installed():
                raise ValueError("Library is not installed")
            for installed_library in self.sources().values():
                if domain in installed_library.domains:
                    continue
                if all(d in self.installed() for d in installed_library.domains):
                    dependencies = self.dependencies(list(installed_library.domains))
                    if domain in dependencies:
                        raise ValueError(
                            f"Remove {', '.join(installed_library.domains)} first; "
                            f"it depends on {domain}."
                        )
            domains = [domain]
        elif action in {"install", "update"}:
            library = self.sources().get(source)
            if library is None:
                raise ValueError("Choose a library from the catalogue")
            sources = self.sources()
            pending = [source]
            planned = set()
            while pending:
                key = pending.pop()
                if key not in sources:
                    raise ValueError(f"Unknown dependency: {key}")
                if key not in planned:
                    planned.add(key)
                    pending.extend(sources[key].dependencies)
            expected = {d for key in planned for d in sources[key].domains}
            results = install_library(source, into=stage)
            received = {d for result in results for d in result.domains}
            for provided in sorted(received):
                installed = self.installed()
                stale = [d for d in SUPERSEDED.get(provided, ()) if d in installed]
                if stale:
                    raise ValueError(superseded_message(provided, stale))
            if received != expected:
                raise ValueError(
                    "Source contents do not match the catalogue's declared domains"
                )
            domains = sorted(
                d for d in received if d in library.domains or d not in self.installed()
            )
            if action == "install" and any(
                safe_path(self.project, d).exists() for d in domains
            ):
                raise ValueError(
                    "A project copy already exists. Review an update instead."
                )
            if action == "update" and not any(
                d in self.installed() for d in library.domains
            ):
                raise ValueError("This library is not installed yet")
            for key in planned:
                requirements.extend(sources[key].requirements)
        else:
            raise ValueError("Unknown library operation")
        for left in domains:
            for right in self.installed():
                if left != right and overlaps(left, right):
                    raise ValueError(
                        f"Cannot replace overlapping library trees: {left} and {right}"
                    )
        before = {d: tree_hash(safe_path(self.project, d)) for d in domains}
        after = (
            {d: tree_hash(safe_path(stage, d)) for d in domains}
            if action != "remove"
            else {}
        )
        changes = []
        for d in domains:
            old = safe_path(self.project, d)
            new = safe_path(stage, d)

            def files(root: Path) -> dict[str, str]:
                return {
                    str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
                    for f in root.rglob("*")
                    if f.is_file()
                    and f.suffix != ".pyc"
                    and "__pycache__" not in f.parts
                }

            old_files, new_files = files(old), files(new)
            changes.append(
                {
                    "domain": d,
                    "added": sorted(new_files.keys() - old_files.keys()),
                    "removed": sorted(old_files.keys() - new_files.keys()),
                    "changed": sorted(
                        f
                        for f in old_files.keys() & new_files.keys()
                        if old_files[f] != new_files[f]
                    ),
                }
            )
        preview = (
            snapshot_catalogue(
                stage, [safe_path(stage, d) / "trusted" for d in domains]
            )
            if action != "remove"
            else {"api": [], "source": []}
        )
        self.review = {
            "id": uuid4().hex,
            "action": action,
            "source": source,
            "domains": domains,
            "before": before,
            "after": after,
            "changes": changes,
            "requirements": sorted(set(requirements)),
            "api": preview["api"],
            "sourceCode": preview["source"],
            "mockDeclarations": mock_declarations(
                self.project if action == "remove" else stage,
                [
                    safe_path(self.project if action == "remove" else stage, d)
                    / "trusted"
                    for d in domains
                ],
            ),
        }
        return self.review

    def apply(self, identifier: str, active: list[str]) -> dict[str, Any]:
        review = self.review
        if review is None or identifier != review["id"] or self.staging is None:
            raise ValueError("This review has expired. Review the change again.")
        stage = Path(self.staging.name)
        for domain in review["domains"]:
            target = safe_path(self.project, domain)
            if any(overlaps(domain, d) for d in active):
                raise ValueError(
                    "Stop the conversation before changing a library it uses."
                )
            if tree_hash(target) != review["before"][domain]:
                raise ValueError(
                    "Project files changed after review. Review the change again."
                )
            if (
                review["action"] != "remove"
                and tree_hash(safe_path(stage, domain)) != review["after"][domain]
            ):
                raise ValueError(
                    "Staged files changed after review. Review the change again."
                )
        # Backups stay on the same filesystem so moves and rollback are atomic.
        backup = ensure_project_dir(self.project) / "library-backups" / review["id"]
        if backup.parent.is_symlink():
            raise ValueError("The backup directory cannot be a symbolic link")
        backup.mkdir(mode=0o700, parents=True)
        moved: list[tuple[Path, Path, bool]] = []
        try:
            for domain in review["domains"]:
                target = safe_path(self.project, domain)
                old = backup / domain
                old.parent.mkdir(parents=True, exist_ok=True)
                existed = target.exists()
                if existed:
                    target.rename(old)
                moved.append((target, old, existed))
                if review["action"] != "remove":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    safe_path(stage, domain).rename(target)
            if review["action"] == "remove":
                self.select([d for d in self.selected if d not in review["domains"]])
        except (OSError, ValueError):
            for target, old, existed in reversed(moved):
                if target.exists():
                    shutil.rmtree(target)
                if existed:
                    old.rename(target)
            raise
        result = {
            "domains": review["domains"],
            "backup": str(backup.relative_to(self.project)),
            "action": review["action"],
        }
        self.discard()
        return result

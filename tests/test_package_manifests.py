"""Shared package setup survives per-domain installation without changing imports."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from clauz3.libraries.discovery import (
    Library,
    LibraryRecord,
    catalog,
    global_catalog,
    library_records,
)
from clauz3.libraries.install import install_layer, install_library
from clauz3.libraries.manifest import (
    PACKAGE_SNAPSHOT,
    ManifestError,
    parse_package,
    read_manifest,
)
from clauz3.libraries.workspace import WorkspaceLibraries


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "store"))


def project_email(project: Path) -> LibraryRecord:
    return next(
        record
        for record in library_records(project, "email")
        if record["visibility"] == "project" and "email" in record["domains"]
    )


def package() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "package",
        "name": "assistant",
        "title": "Assistant tools",
        "requirements": {
            "python": ["shared>=1"],
            "tools": [{"name": "gog", "description": "Install gog"}],
            "setup": [
                {
                    "id": "account",
                    "kind": "configuration",
                    "title": "Select account",
                    "description": "The intended account",
                    "instructions": "Choose an account in gog.",
                }
            ],
        },
    }


def source(root: Path) -> Path:
    for name in ("email", "calendar"):
        directory = root / "tools" / name
        (directory / "trusted").mkdir(parents=True)
        (directory / "trusted/effects.py").write_text(
            "raise AssertionError('no imports')\n"
        )
        (directory / "clauz3.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "name": name,
                    "title": name.title(),
                    "summary": f"{name} summary",
                    "description": f"{name} overview",
                    "requirements": {"python": ["shared>=1", f"{name}>=1"]},
                }
            )
        )
    (root / "clauz3.yaml").write_text(yaml.safe_dump(package()))
    (root / "tests").mkdir()
    (root / "tests/test_secret.py").write_text(
        "raise AssertionError('never execute')\n"
    )
    return root


def test_package_install_preserves_authored_manifests_and_shared_setup(
    tmp_path: Path,
) -> None:
    root = source(tmp_path / "source")
    project = tmp_path / "project"
    install_library(str(root), into=project, generate_skills=True)
    for name in ("email", "calendar"):
        installed = project / ".clauz3/tools" / name
        assert (installed / "clauz3.yaml").read_bytes() == (
            root / "tools" / name / "clauz3.yaml"
        ).read_bytes()
        assert yaml.safe_load((installed / PACKAGE_SNAPSHOT).read_text()) == package()
        manifest = read_manifest(installed, name)
        assert manifest is not None
        assert manifest["title"] == name.title()
        assert manifest["requirements"]["python"] == ["shared>=1", f"{name}>=1"]
        skill = (project / ".clauz3/skills" / name / "SKILL.md").read_text()
        assert "Select account" in skill
        assert "Install gog" in skill
    assert not (project / ".clauz3/packages").exists()
    assert not (project / "tests").exists()
    assert not list((project / ".clauz3").rglob("test_secret.py"))
    (root / "clauz3.yaml").unlink()
    record = project_email(project)
    assert "Select account" in record["requirements"]
    assert record["domains"] == ["calendar", "email"]


def test_force_update_refreshes_and_removes_package_snapshot(tmp_path: Path) -> None:
    root = source(tmp_path / "source")
    project = tmp_path / "project"
    install_layer(root, into=project)
    data = package()
    data["requirements"]["tools"][0]["description"] = "Updated CLI setup"
    (root / "clauz3.yaml").write_text(yaml.safe_dump(data))
    install_layer(root / "tools", into=project, force=True)
    assert "Updated CLI setup" in project_email(project)["requirements"]
    (root / "clauz3.yaml").unlink()
    install_layer(root, into=project, force=True)
    assert not list((project / ".clauz3/tools").rglob(PACKAGE_SNAPSHOT))
    assert "Updated CLI setup" not in project_email(project)["requirements"]


def test_global_copy_retains_setup_when_original_source_is_gone(tmp_path: Path) -> None:
    root = source(tmp_path / "source")
    install_library(str(root), into=tmp_path / "store", globally=True)
    shutil.rmtree(root)
    assert all("Select account" in lib.requirements for lib in global_catalog())
    project = tmp_path / "project"
    install_library("global:email", into=project, generate_skills=True)
    assert (project / ".clauz3/tools/email" / PACKAGE_SNAPSHOT).is_file()
    assert not (project / ".clauz3/tools/calendar").exists()
    assert "Select account" in project_email(project)["requirements"]


@pytest.mark.parametrize(
    "mutation",
    [
        {"kind": "library"},
        {"schema_version": 2},
        {"schema_version": True},
        {"name": "../escape"},
        {"domains": ["email"]},
        {"summary": "Not package-owned"},
        {"requirements": {"tokens": "secret"}},
        {"documentation": "javascript:alert(1)"},
    ],
)
def test_invalid_package_is_refused_before_copy(
    tmp_path: Path, mutation: dict[str, Any]
) -> None:
    root = source(tmp_path / "source")
    data = package()
    data.update(mutation)
    (root / "clauz3.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ManifestError):
        install_library(str(root), into=tmp_path / "project")
    assert not (tmp_path / "project/.clauz3/tools").exists()


def test_shared_steps_cannot_claim_domain_specific_effects() -> None:
    data = package()
    data["requirements"]["setup"][0]["applies_to"] = ["send_email"]
    with pytest.raises(ManifestError, match="applies_to"):
        parse_package(yaml.safe_dump(data), label="package")


@pytest.mark.parametrize("field", ["setup", "tools"])
def test_conflicting_shared_requirements_fail_preflight(
    tmp_path: Path, field: str
) -> None:
    root = source(tmp_path / "source")
    path = root / "tools/email/clauz3.yaml"
    data = yaml.safe_load(path.read_text())
    entry = package()["requirements"][field][0]
    entry["description"] = "A conflicting definition"
    data["requirements"][field] = [entry]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ManifestError, match=f"conflicting {field}"):
        install_layer(root, into=tmp_path / "project")
    assert not (tmp_path / "project/.clauz3/tools").exists()


def test_identical_shared_entries_are_deduplicated(tmp_path: Path) -> None:
    root = source(tmp_path / "source")
    path = root / "tools/email/clauz3.yaml"
    data = yaml.safe_load(path.read_text())
    data["requirements"] = package()["requirements"]
    path.write_text(yaml.safe_dump(data))
    install_layer(root, into=tmp_path / "project")
    merged = read_manifest(tmp_path / "project/.clauz3/tools/email", "email")
    assert merged is not None
    assert merged["requirements"] == package()["requirements"]


def test_package_requires_domains_to_own_their_descriptions(tmp_path: Path) -> None:
    root = source(tmp_path / "source")
    (root / "tools/email/clauz3.yaml").unlink()
    with pytest.raises(ManifestError, match="manifest for every domain"):
        install_library(str(root), into=tmp_path / "project")
    assert not (tmp_path / "project/.clauz3/tools").exists()


def test_package_symlink_is_refused_including_dangling_link(tmp_path: Path) -> None:
    root = source(tmp_path / "source")
    (root / "clauz3.yaml").unlink()
    (root / "clauz3.yaml").symlink_to(tmp_path / "missing")
    with pytest.raises(ManifestError, match="symbolic link"):
        install_layer(root, into=tmp_path / "project")


def test_remote_cache_and_authored_discovery_include_shared_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = source(tmp_path / "commit")
    assert "Select account" in project_email(root)["requirements"]
    remote = "gh:normalform-ai/clauz3-tools-autolabs"
    monkeypatch.setattr(
        "clauz3.libraries.install.resolve_remote_source", lambda _: root
    )
    install_library(remote, into=tmp_path / "project")
    (cached,) = [entry for entry in catalog() if entry.source == remote]
    assert "Select account" in cached.requirements


def test_workspace_review_includes_shared_metadata_and_detects_its_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = source(tmp_path / "source")
    monkeypatch.setattr(
        "clauz3.libraries.workspace.catalog",
        lambda: [Library(str(root), "Unknown", ())],
    )
    project = tmp_path / "project"
    project.mkdir()
    manager = WorkspaceLibraries(project, tmp_path / "workspace", [])
    try:
        review = manager.prepare("install", str(root), "")
        assert "Select account" in review["requirements"]
        assert all(PACKAGE_SNAPSHOT in change["added"] for change in review["changes"])
        assert all(
            m["requirements"]["tools"][0]["name"] == "gog" for m in review["manifests"]
        )
    finally:
        manager.discard()
    install_library(str(root), into=project)
    before = manager.fingerprint(["email"])
    snapshot = project / ".clauz3/tools/email" / PACKAGE_SNAPSHOT
    data = yaml.safe_load(snapshot.read_text())
    data["requirements"]["tools"][0]["description"] = "Changed"
    snapshot.write_text(yaml.safe_dump(data))
    assert manager.fingerprint(["email"]) != before
    origin = json.loads((snapshot.parent / ".clauz3-origin.json").read_text())
    assert origin["source"] == str(root)

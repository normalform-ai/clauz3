"""Library-owned metadata is declarative, validated, and revision-specific."""

import json
from pathlib import Path

import pytest
import yaml

from clauz3.libraries.discovery import (
    Library,
    catalog,
    global_catalog,
    library_records,
)
from clauz3.libraries.install import InstallError, install_layer, install_library
from clauz3.libraries.manifest import ManifestError, parse_manifest, read_manifest
from clauz3.libraries.workspace import WorkspaceLibraries


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "data"))


def library(
    root: Path, name: str = "demo", dependencies: list[str] | None = None
) -> Path:
    domain = root / "tools" / name
    trusted = domain / "trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text("raise AssertionError('must never import')\n")
    (domain / "clauz3.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "name": name,
                "title": "Demo",
                "summary": "Library-owned summary",
                "description": "A human overview.",
                "dependencies": dependencies or [],
                "requirements": {
                    "python": ["example>=1"],
                    "tools": [{"name": "example", "description": "An external CLI"}],
                    "setup": [
                        {
                            "id": "login",
                            "kind": "authentication",
                            "title": "Sign in",
                            "description": "User-managed authorization",
                            "instructions": (
                                "Authorize in your terminal; "
                                "keep tokens outside this file."
                            ),
                            "documentation": "https://example.com/setup",
                            "permissions": ["Write drafts"],
                            "applies_to": ["create_draft"],
                        }
                    ],
                },
            }
        )
    )
    return root


def test_installed_manifest_is_authoritative_and_needs_no_registry(
    tmp_path: Path,
) -> None:
    source = library(tmp_path / "source")
    project = tmp_path / "project"
    results = install_library(str(source), into=project, generate_skills=True)
    assert all(not result.warnings for result in results)
    skill = (project / ".clauz3/skills/demo/SKILL.md").read_text()
    assert "## User setup" in skill
    assert "User-managed authorization" in skill
    path = source / "tools/demo/clauz3.yaml"
    path.write_text(
        path.read_text().replace("Library-owned summary", "Changed upstream")
    )
    (record,) = library_records(project, "Library-owned summary")
    assert record["installed"]
    assert record["source"] == str(source)
    assert record["title"] == "Demo"
    assert record["manifests"][0]["description"] == "A human overview."
    assert record["requirements"] == [
        "Python package example>=1",
        "An external CLI",
        "Sign in",
    ]
    assert not library_records(project, "Changed upstream")
    install_library(str(source), into=project, force=True)
    assert library_records(project, "Changed upstream")


def test_manifest_dependencies_are_installed_and_existing_copies_preserved(
    tmp_path: Path,
) -> None:
    source = library(tmp_path / "source", dependencies=["stdlib:filesystem"])
    project = tmp_path / "project"
    results = install_library(str(source), into=project)
    assert [result.domains for result in results] == [["filesystem"], ["demo"]]
    marker = project / ".clauz3/tools/filesystem/operator.txt"
    marker.write_text("keep")
    install_library(str(source), into=project, force=True)
    assert marker.read_text() == "keep"


def test_global_metadata_stores_origin_without_copying_descriptions(
    tmp_path: Path,
) -> None:
    source = library(tmp_path / "source")
    store = tmp_path / "data"
    install_library(str(source), into=store, globally=True)
    sidecar = json.loads((store / "metadata/demo/library.json").read_text())
    assert sidecar == {"origin": str(source)}
    (record,) = global_catalog()
    assert record.description == "Library-owned summary"
    assert record.origin == str(source)


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"name": "../escape"},
        {"name": "different"},
        {"summary": ""},
        {"unknown": "typo"},
        {"documentation": "javascript:alert(1)"},
        {"requirements": {"tokens": "do not store secrets"}},
        {
            "requirements": {
                "setup": [
                    {
                        "id": "x",
                        "kind": "execute",
                        "title": "X",
                        "description": "x",
                        "instructions": "x",
                    }
                ]
            }
        },
    ],
)
def test_invalid_manifest_refused_before_install(
    tmp_path: Path, mutation: dict[str, object]
) -> None:
    source = library(tmp_path / "source")
    path = source / "tools/demo/clauz3.yaml"
    data = yaml.safe_load(path.read_text())
    data.update(mutation)
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ManifestError):
        install_library(str(source), into=tmp_path / "project")
    assert not (tmp_path / "project/.clauz3/tools").exists()


@pytest.mark.parametrize(
    "text",
    [
        "!!python/object/apply:os.system ['do not execute']",
        "schema_version: 1\nschema_version: 2\n",
        "value: &x [*x]\n",
        "- not-a-mapping\n",
    ],
)
def test_yaml_cannot_instantiate_objects_or_hide_duplicate_values(text: str) -> None:
    with pytest.raises(ManifestError):
        parse_manifest(text, label="test manifest", domain="demo")


def test_optional_nulls_are_normalized(tmp_path: Path) -> None:
    source = library(tmp_path)
    directory = source / "tools/demo"
    data = yaml.safe_load((directory / "clauz3.yaml").read_text())
    data["requirements"] = None
    data["dependencies"] = None
    manifest = parse_manifest(yaml.safe_dump(data), label="null slots", domain="demo")
    assert "requirements" not in manifest
    assert "dependencies" not in manifest


def test_all_manifests_checked_before_any_domain_is_copied(tmp_path: Path) -> None:
    source = library(tmp_path / "source", "first")
    library(source, "second")
    (source / "tools/second/clauz3.yaml").write_text("invalid: manifest\n")
    with pytest.raises(ManifestError):
        install_layer(source, into=tmp_path / "project")
    assert not (tmp_path / "project/.clauz3/tools").exists()


def test_remote_metadata_is_cached_only_after_explicit_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = "gh:normalform-ai/clauz3-tools-autolabs"
    checkout = library(tmp_path / "commit", "email")
    calls: list[str] = []

    def resolve(value: str) -> Path:
        calls.append(value)
        return checkout

    monkeypatch.setattr("clauz3.libraries.install.resolve_remote_source", resolve)
    (unknown,) = [lib for lib in catalog() if lib.source == source]
    assert not unknown.domains
    assert calls == []
    install_library(source, into=tmp_path / "project")
    assert calls == [source]
    (cached,) = [lib for lib in catalog() if lib.source == source]
    assert cached.domains == ("email",)
    assert cached.description == "Library-owned summary"
    assert calls == [source]  # Listing uses cached data without fetching or imports.


def test_remote_dependency_cycles_are_refused_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = library(tmp_path / "a", "a", ["gh:test/b"])
    b = library(tmp_path / "b", "b", ["gh:test/a"])
    monkeypatch.setattr(
        "clauz3.libraries.install.resolve_remote_source",
        lambda value: {"gh:test/a": a, "gh:test/b": b}[value],
    )
    with pytest.raises(InstallError, match="cyclic library dependency"):
        install_library("gh:test/a", into=tmp_path / "project")
    assert not (tmp_path / "project/.clauz3/tools").exists()


def test_workspace_review_uses_fetched_setup_before_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = library(tmp_path / "source")
    monkeypatch.setattr(
        "clauz3.libraries.workspace.catalog",
        lambda: [Library(source=str(source), description="Unknown", domains=())],
    )
    project = tmp_path / "project"
    project.mkdir()
    manager = WorkspaceLibraries(project, tmp_path / "workspace", [])
    try:
        review = manager.prepare("install", str(source), "")
        assert review["domains"] == ["demo"]
        assert "Sign in" in review["requirements"]
        assert (
            review["manifests"][0]["requirements"]["setup"][0]["kind"]
            == "authentication"
        )
        assert not (project / ".clauz3/tools/demo").exists()
    finally:
        manager.discard()


def test_symlinked_manifest_is_not_read(tmp_path: Path) -> None:
    domain = tmp_path / "demo"
    domain.mkdir()
    secret = tmp_path / "outside"
    secret.write_text("outside data")
    (domain / "clauz3.yaml").symlink_to(secret)
    with pytest.raises(ManifestError, match="symbolic link"):
        read_manifest(domain, "demo")


def test_workspace_warns_about_legacy_metadata_without_blocking_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = library(tmp_path / "source")
    (source / "tools/demo/clauz3.yaml").unlink()
    monkeypatch.setattr(
        "clauz3.libraries.workspace.catalog",
        lambda: [Library(source=str(source), description="Legacy", domains=("demo",))],
    )
    project = tmp_path / "project"
    project.mkdir()
    manager = WorkspaceLibraries(project, tmp_path / "workspace", [])
    try:
        review = manager.prepare("install", str(source), "")
        assert len(review["warnings"]) == 1
        assert "missing tools/demo/clauz3.yaml" in review["warnings"][0]
        assert review["manifests"] == []
        manager.apply(review["id"], [])
        assert (project / ".clauz3/tools/demo/trusted/effects.py").is_file()
    finally:
        manager.discard()

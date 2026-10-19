"""Bundled email installs offline and composes with filesystem-only programs."""

import json
import shutil
from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.libraries.discovery import catalog, installed_libraries
from clauz3.libraries.install import InstallError, install_library
from clauz3.libraries.layout import expand_import_roots
from clauz3.libraries.workspace import WorkspaceLibraries
from clauz3.prover import prove_text
from clauz3.sessions.catalogue import snapshot_catalogue


@pytest.fixture(autouse=True)
def offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "global"))

    def no_fetch(source: str) -> Path:
        raise AssertionError(f"Bundled email must not fetch {source}")

    monkeypatch.setattr("clauz3.libraries.install.resolve_remote_source", no_fetch)


def test_email_is_bundled_with_setup_and_documentation(tmp_path: Path) -> None:
    sources = {library.source: library for library in catalog()}
    assert "gh:normalform-ai/clauz3-tools-assistant" not in sources
    email = sources["stdlib:google_workspace"]
    assert email.domains == ("google_workspace/gmail",)
    assert email.visibility == "bundled"
    assert email.title == "Google Workspace"
    assert installed_libraries(tmp_path) == []
    assert "gog" in email.description
    requirements = email.manifests[0]["requirements"]
    assert requirements["tools"][0]["name"] == "gog"
    assert {step["id"] for step in requirements["setup"]} >= {
        "google-account",
        "gmail-oauth",
        "email-backend",
    }
    results = install_library(
        "stdlib:google_workspace", into=tmp_path, generate_skills=True
    )
    assert all(not result.warnings for result in results)
    root = tmp_path / ".clauz3/tools/google_workspace/gmail"
    api = "\n".join(snapshot_catalogue(tmp_path, [root / "trusted"])["api"])
    assert "Effect: create_draft(" in api
    assert "Effect: send_email(" in api
    assert "*, dry_run: bool=True" in api
    skill = (tmp_path / ".clauz3/skills/google_workspace/gmail/SKILL.md").read_text()
    assert "gog" in skill and "Gmail" in skill
    assert "Save an unsent email" in skill


def test_selected_email_does_not_block_filesystem_program(tmp_path: Path) -> None:
    for source in ("stdlib:google_workspace", "stdlib:filesystem"):
        install_library(source, into=tmp_path)
    roots = [
        tmp_path / f".clauz3/tools/{domain}/trusted"
        for domain in ("google_workspace/gmail", "filesystem")
    ]
    source = """import clauz3
from tools.filesystem.trusted.effects import read_file
from tools.filesystem.trusted import contracts as fs

@clauz3.guarantee(fs.read_only())
@clauz3.guarantee(fs.only_reads_files([clauz3.FilePath("tmp/emails.csv")]))
@clauz3.guarantee(fs.reads_at_most(1))
def main() -> None:
    print(read_file("tmp/emails.csv"))
"""
    proofs = prove_text(
        source, trusted_roots=roots, import_paths=expand_import_roots([tmp_path])
    )
    assert proofs and all(p.proof.conclusion == Conclusion.OK for p in proofs)


def test_migration_backs_up_old_email_and_requires_explicit_selection(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    old_root = project / ".clauz3/tools/email"
    (old_root / "trusted").mkdir(parents=True)
    old = "# Existing operator email library\n"
    (old_root / "trusted/effects.py").write_text(old)
    (old_root / ".clauz3-origin.json").write_text(
        json.dumps({"source": "gh:normalform-ai/clauz3-tools-assistant"})
    )
    with pytest.raises(InstallError, match="stdlib:google_workspace now provides"):
        install_library("stdlib:google_workspace", into=project, force=True)
    assert not (project / ".clauz3/tools/google_workspace").exists()
    manager = WorkspaceLibraries(project, tmp_path / "state", ["email"])
    try:
        with pytest.raises(ValueError, match="stdlib:google_workspace now provides"):
            manager.prepare("install", "stdlib:google_workspace", "")
        review = manager.prepare("remove", "", "email")
        assert (old_root / "trusted/effects.py").read_text() == old
        result = manager.apply(review["id"], [])
        backup = project / result["backup"] / "email/trusted/effects.py"
        assert backup.read_text() == old
        assert manager.selected == []
        review = manager.prepare("install", "stdlib:google_workspace", "")
        assert review["domains"] == ["google_workspace/gmail"]
        assert not review["warnings"]
        manager.apply(review["id"], [])
        root = project / ".clauz3/tools/google_workspace/gmail"
        assert (
            json.loads((root / ".clauz3-origin.json").read_text())["source"]
            == "stdlib:google_workspace"
        )
        assert "*, dry_run=True:" in (root / "trusted/effects.py").read_text()
        assert installed_libraries(project)[0].title == "Google Workspace"
        assert manager.selected == []
        assert manager.select(["google_workspace/gmail"]) == ["google_workspace/gmail"]
    finally:
        manager.discard()


def test_bundle_discovers_services_without_enabling_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A future service can join the bundle without a registry/domain list change.
    import clauz3.libraries.discovery as discovery
    import clauz3.libraries.install as installation

    bundled = tmp_path / "distribution"
    bundle = bundled / "stdlib/google_workspace"
    source = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib/google_workspace"
    shutil.copytree(source, bundle, ignore=shutil.ignore_patterns("__pycache__"))
    (bundled / "library_registry.yaml").write_text(
        "sources: [stdlib:google_workspace]\n"
    )
    service = bundle / "tools/google_workspace/sheets"
    (service / "trusted").mkdir(parents=True)
    (service / "trusted/effects.py").write_text(
        "# Test service, no executable effects\n"
    )
    (service / "clauz3.yaml").write_text(
        "schema_version: 1\nname: google_workspace/sheets\ntitle: Sheets\n"
        "summary: Test sheet service\ndescription: Test service metadata.\n"
    )
    monkeypatch.setattr(discovery, "files", lambda _: bundled)
    monkeypatch.setattr(installation, "files", lambda _: bundled)
    (library,) = catalog()
    assert library.title == "Google Workspace"
    assert library.domains == ("google_workspace/gmail", "google_workspace/sheets")
    project = tmp_path / "project"
    install_library("stdlib:google_workspace", into=project, generate_skills=True)
    assert (project / ".clauz3/skills/google_workspace/sheets/SKILL.md").is_file()
    manager = WorkspaceLibraries(project, tmp_path / "state", [])
    assert manager.selected == []
    assert manager.select(["google_workspace/gmail"]) == ["google_workspace/gmail"]
    assert manager.roots(manager.selected) == [
        project / ".clauz3/tools/google_workspace/gmail/trusted"
    ]
    assert manager.select(["google_workspace/sheets"]) == ["google_workspace/sheets"]

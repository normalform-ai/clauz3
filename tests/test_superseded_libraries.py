"""Libraries merged into stdlib:filesystem cannot be proved beside it.

An old copy of coreutils, editor, find, grep or text defines effects named like
filesystem's (``remove``, ``append_file``, ``find``, ...), so every proof using
both is refused as a duplicate effect. Installing the new filesystem next to
one, from the CLI or the workspace, is refused up front with the migration
steps, and the prover's refusal carries the same advice.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from clauz3.libraries.install import InstallError, install_library
from clauz3.libraries.workspace import WorkspaceLibraries
from clauz3.prover import ProverConfigError, prove_text

OLD = ["coreutils", "editor", "find", "grep", "text"]


def _old_copy(project: Path, domain: str) -> Path:
    """A leftover project copy whose effect collides with filesystem's."""

    trusted = project / ".clauz3/tools" / domain / "trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text(
        "import clauz3\n@clauz3.trusted\ndef remove(path: str) -> None:\n    pass\n"
    )
    return trusted


@pytest.mark.parametrize("domain", OLD)
def test_install_refuses_beside_a_superseded_copy(tmp_path: Path, domain: str) -> None:
    _old_copy(tmp_path, domain)
    with pytest.raises(InstallError, match=f"now provides the tools of {domain}"):
        install_library("stdlib:filesystem", into=tmp_path, force=True)
    assert not (tmp_path / ".clauz3/tools/filesystem").exists()


def test_install_proceeds_once_the_old_copy_is_removed(tmp_path: Path) -> None:
    _old_copy(tmp_path, "find")
    with pytest.raises(InstallError):
        install_library("stdlib:filesystem", into=tmp_path)
    shutil.rmtree(tmp_path / ".clauz3/tools/find")
    install_library("stdlib:filesystem", into=tmp_path)
    assert (tmp_path / ".clauz3/tools/filesystem/trusted/events.py").is_file()


def test_workspace_review_refuses_beside_a_superseded_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "global"))
    project = tmp_path / "project"
    _old_copy(project, "coreutils")
    manager = WorkspaceLibraries(project, tmp_path / "state", [])
    with pytest.raises(ValueError, match="now provides the tools of coreutils"):
        manager.prepare("install", "stdlib:filesystem", "")


def test_the_duplicate_effect_refusal_says_how_to_migrate(tmp_path: Path) -> None:
    old = _old_copy(tmp_path, "coreutils")
    install_library("stdlib:filesystem", into=tmp_path / "fresh")
    new = tmp_path / "fresh/.clauz3/tools/filesystem/trusted"
    source = "def main() -> None:\n    pass\n"
    with pytest.raises(ProverConfigError) as refused:
        prove_text(
            source,
            trusted_roots=[new, old],
            import_paths=[tmp_path / "fresh/.clauz3", tmp_path / ".clauz3"],
        )
    message = str(refused.value)
    assert "two trusted effects are called 'remove'" in message
    assert "stdlib:filesystem now provides the tools of coreutils" in message

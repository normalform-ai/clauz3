import shutil
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture
from _pytest.monkeypatch import MonkeyPatch

from clauz3.cli import main
from clauz3.libraries.discovery import installed_domains
from clauz3.libraries.install import InstallError, install_layer
from clauz3.libraries.layout import (
    LayoutError,
    domain_name,
    expand_import_roots,
)
from clauz3.prover import ProverConfigError, prove_path
from clauz3.runner import discover_trusted_roots
from clauz3.sessions.catalogue import snapshot_catalogue

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
PASS_CASE = ROOT / "src/clauz3/stdlib/filesystem/tests/cases/count_lines_pass.py"
FAIL_CASE = (
    ROOT / "src/clauz3/stdlib/filesystem/tests/cases/count_lines_outside_fail.py"
)


def test_install_uses_dot_clauz3_and_ignores_caches(tmp_path: Path) -> None:
    result = install_layer("stdlib:filesystem", into=tmp_path)

    assert result.dest_tools == tmp_path / ".clauz3/tools"
    assert (tmp_path / ".clauz3/tools/filesystem/trusted/effects.py").is_file()
    assert not (tmp_path / "tools").exists()
    ignored = (tmp_path / ".clauz3/.gitignore").read_text().splitlines()
    assert {"__pycache__/", "*.pyc", "approval-service.json"} <= set(ignored)


def test_import_roots_put_installed_layer_first_and_are_idempotent(
    tmp_path: Path,
) -> None:
    # The selection is the same before and after the first install, so an
    # approval layer authorized at session start keeps matching later runs.
    before = expand_import_roots([tmp_path])
    install_layer("stdlib:filesystem", into=tmp_path)

    expanded = expand_import_roots([tmp_path])
    assert expanded == before == [tmp_path / ".clauz3", tmp_path]
    assert expand_import_roots(expanded) == expanded
    assert expand_import_roots([tmp_path, tmp_path]) == expanded


def test_installed_and_authored_layers_are_both_discovered(tmp_path: Path) -> None:
    install_layer("stdlib:filesystem", into=tmp_path)
    shutil.copytree(EMAIL_ROOT / "tools", tmp_path / "tools")

    roots = discover_trusted_roots(import_roots=expand_import_roots([tmp_path]))

    assert roots == [
        tmp_path / ".clauz3/tools/filesystem/trusted",
        tmp_path / "tools/email/trusted",
    ]
    assert [domain_name(tmp_path, root) for root in roots] == ["filesystem", "email"]
    assert installed_domains(tmp_path) == {"filesystem", "email"}


def test_same_domain_in_both_locations_is_refused(tmp_path: Path) -> None:
    install_layer("stdlib:filesystem", into=tmp_path)
    shutil.copytree(
        tmp_path / ".clauz3/tools/filesystem", tmp_path / "tools/filesystem"
    )

    with pytest.raises(LayoutError, match="filesystem provided by both"):
        expand_import_roots([tmp_path])


def test_regular_tools_package_is_refused(tmp_path: Path) -> None:
    install_layer("stdlib:filesystem", into=tmp_path)
    shutil.copytree(EMAIL_ROOT / "tools", tmp_path / "tools")
    (tmp_path / "tools/__init__.py").write_text("")

    with pytest.raises(LayoutError, match="regular package"):
        discover_trusted_roots(import_roots=expand_import_roots([tmp_path]))


def test_install_refuses_domain_the_project_authors(tmp_path: Path) -> None:
    shutil.copytree(EMAIL_ROOT / "tools", tmp_path / "tools")

    with pytest.raises(InstallError, match="project-authored"):
        install_layer(EMAIL_ROOT, into=tmp_path, force=True)
    assert not (tmp_path / ".clauz3").exists()


def test_global_store_keeps_its_own_tools_directory(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    store = tmp_path / "store"
    monkeypatch.setenv("CLAUZ3_HOME", str(store))

    assert main(["install", "-g", "stdlib:filesystem"]) == 0
    assert (store / "tools/filesystem/trusted").is_dir()
    assert not (store / ".clauz3").exists()

    project = tmp_path / "project"
    project.mkdir()
    assert main(["install", "global:filesystem", "--into", str(project)]) == 0
    assert (project / ".clauz3/tools/filesystem/trusted").is_dir()


def test_prove_and_tools_resolve_installed_layer(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    install_layer("stdlib:filesystem", into=tmp_path)
    shutil.copytree(EMAIL_ROOT / "tools", tmp_path / "tools")
    cases = tmp_path / "cases"
    cases.mkdir()
    shutil.copy(PASS_CASE, cases)
    shutil.copy(FAIL_CASE, cases)
    monkeypatch.chdir(tmp_path)
    trusted = ".clauz3/tools/filesystem/trusted"

    assert main(["prove", "--trusted-root", trusted, f"cases/{PASS_CASE.name}"]) == 0
    assert main(["prove", "--trusted-root", trusted, f"cases/{FAIL_CASE.name}"]) == 1
    assert (
        main(
            [
                "prove",
                "--trusted-root",
                trusted,
                "--import-root",
                str(tmp_path / ".clauz3"),
                "--import-root",
                str(tmp_path),
                f"cases/{PASS_CASE.name}",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["tools"]) == 0
    listed = capsys.readouterr().out
    assert "read_lines" in listed
    assert "send_email" in listed


def test_layout_conflict_is_a_cli_error(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    install_layer("stdlib:filesystem", into=tmp_path)
    shutil.copytree(
        tmp_path / ".clauz3/tools/filesystem", tmp_path / "tools/filesystem"
    )
    monkeypatch.chdir(tmp_path)

    assert main(["tools"]) == 2
    assert "provided by both" in capsys.readouterr().err
    program = tmp_path / "program.py"
    program.write_text("def main() -> None:\n    pass\n")
    # Nothing was proved or run: a configuration error, not a runtime error.
    assert main(["run", str(program)]) == 2
    error = capsys.readouterr().err
    assert error.startswith("error: ") and "provided by both" in error
    assert main(["prove", str(program)]) == 2
    with pytest.raises(ProverConfigError, match="provided by both"):
        prove_path(program)


def test_catalogue_names_installed_modules_by_import_path(tmp_path: Path) -> None:
    install_layer("stdlib:filesystem", into=tmp_path)
    roots = discover_trusted_roots(import_roots=expand_import_roots([tmp_path]))

    api = "\n".join(snapshot_catalogue(tmp_path, roots)["api"])

    assert "from tools.filesystem.trusted.effects import read_lines" in api
    assert ".clauz3.tools" not in api


def test_generated_skill_names_its_install_location(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    project = install_layer("stdlib:filesystem", into=tmp_path, generate_skills=True)
    text = project.skills[0].read_text()
    assert project.skills == [tmp_path / ".clauz3/skills/filesystem/SKILL.md"]
    assert "installed in `.clauz3/tools/filesystem/trusted/`" in text

    store = tmp_path / "store"
    monkeypatch.setenv("CLAUZ3_HOME", str(store))
    assert main(["install", "-g", "--skills", "stdlib:filesystem"]) == 0
    text = (store / "skills/filesystem/SKILL.md").read_text()
    assert "clauz3 install global:filesystem" in text

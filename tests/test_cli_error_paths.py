"""CLI failures a user can cause are reported as ``error: ...``, not tracebacks.

Exit 2 means the command could not be carried out as asked (a bad source, an
unreadable program, a broken trusted file); exit 1 is reserved for a program
that did not prove or that raised after it was proved and approved.
"""

import json
import subprocess
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from clauz3.cli import main
from clauz3.libraries.discovery import (
    _catalog_cache,
    catalog,
    format_libraries,
    library_records,
)
from clauz3.libraries.manifest import ManifestError

EFFECT = "import clauz3\n\n\n@clauz3.trusted\ndef act(x: str) -> None:\n    pass\n"


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUZ3_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("CLAUZ3_HOME", str(tmp_path / "store"))


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    repo = tmp_path / "upstream"
    (repo / "tools/x/trusted").mkdir(parents=True)
    (repo / "tools/x/trusted/effects.py").write_text("# placeholder\n")
    for command in (
        ["init", "--quiet", "-b", "main"],
        ["add", "-A"],
        [
            *("-c", "user.email=test@example.com", "-c", "user.name=Test"),
            *("commit", "--quiet", "-m", "init"),
        ],
    ):
        subprocess.run(["git", "-C", str(repo), *command], check=True)
    return repo


def _reported(capsys: CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err
    return captured.err


@pytest.mark.parametrize("command", ["install", "test"])
def test_an_unreachable_remote_source_is_an_error_not_a_traceback(
    command: str, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    missing = (tmp_path / "no-such-repo.git").as_uri()
    argv = [command, missing]
    if command == "install":
        argv += ["--into", str(tmp_path / "project")]

    assert main(argv) == 2

    assert "git ls-remote failed" in _reported(capsys)
    assert not (tmp_path / "project").exists()


@pytest.mark.parametrize("command", ["install", "test"])
def test_an_unknown_ref_is_an_error_not_a_traceback(
    command: str, upstream: Path, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    argv = [command, f"{upstream.as_uri()}@no-such-ref"]
    if command == "install":
        argv += ["--into", str(tmp_path / "project")]

    assert main(argv) == 2

    assert "'no-such-ref' not found" in _reported(capsys)


def test_policy_check_reports_an_unreadable_program(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"version": 1, "rules": []}))
    missing = tmp_path / "missing.py"

    assert main(["policy-check", str(missing), "--policy", str(policy)]) == 2

    assert str(missing) in _reported(capsys)


def test_run_reports_an_unreadable_program_as_a_configuration_error(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """Exit 1 is for a proved program that raised; nothing ran here."""
    missing = tmp_path / "missing.py"

    assert main(["run", str(missing)]) == 2
    error = _reported(capsys)
    assert str(missing) in error
    assert "runtime error" not in error
    # ``prove`` already reports the same input the same way.
    assert main(["prove", str(missing)]) == 2
    assert capsys.readouterr().err == error


def test_run_reports_an_undecodable_program_as_a_configuration_error(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    program = tmp_path / "program.py"
    program.write_bytes(b"x = '\xff'\n")

    assert main(["run", str(program)]) == 2

    assert str(program) in _reported(capsys)


def test_tools_reports_a_trusted_file_with_a_syntax_error(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    trusted = tmp_path / "tools/home/trusted"
    trusted.mkdir(parents=True)
    (trusted / "effects.py").write_text(EFFECT)
    broken = trusted / "zz_broken.py"
    broken.write_text("def broken(:\n")

    assert main(["tools", "--import-root", str(tmp_path)]) == 2

    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert str(broken) in captured.err
    assert "Traceback" not in captured.err
    # A partial listing would read as the whole vocabulary.
    assert captured.out == ""


def test_tools_reports_a_missing_trusted_root(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    missing = tmp_path / "tools/nope/trusted"

    assert main(["tools", "--trusted-root", str(missing)]) == 2

    assert str(missing) in _reported(capsys)


@pytest.mark.parametrize(
    "cached",
    [
        {"source": "gh:x/y", "revision": "abc"},
        {"domains": ["email"]},
        {"domains": {"email": "not a manifest"}},
        ["domains"],
    ],
)
def test_a_malformed_catalogue_cache_is_reported_with_its_path(
    cached: object,
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = next(lib.source for lib in catalog() if lib.visibility == "repository")
    path = _catalog_cache(source)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(cached))

    with pytest.raises(ManifestError, match="catalogue cache"):
        catalog()

    monkeypatch.chdir(tmp_path)
    assert main(["libraries"]) == 2
    assert str(path) in _reported(capsys)
    assert main(["install", "stdlib:env", "--into", str(tmp_path / "project")]) == 2
    assert str(path) in _reported(capsys)


def test_an_authored_domain_is_listed_without_an_install_command(
    tmp_path: Path,
) -> None:
    domain = tmp_path / "tools/home"
    (domain / "trusted").mkdir(parents=True)
    (domain / "trusted/effects.py").write_text(EFFECT)
    (domain / "clauz3.yaml").write_text(
        "schema_version: 1\nname: home\ntitle: Home\nsummary: Home tools\n"
        "description: Authored in this project.\n"
    )

    records = library_records(tmp_path)
    (authored,) = [r for r in records if r["source"] == "project:home"]
    bundled = next(r for r in records if r["source"] == "stdlib:env")

    # ``project:`` is not an install source; the domain is already here.
    assert authored["install_command"] == ""
    assert bundled["install_command"] == "clauz3 install stdlib:env"
    listing = format_libraries([authored, bundled])
    assert "clauz3 install project:" not in listing
    assert listing.count("  Install: ") == 1
    assert "  Install: clauz3 install stdlib:env" in listing

"""Library discovery, explicit global selection and first-run setup."""

import json
import sys
from pathlib import Path

import pytest

from clauz3.cli import main
from clauz3.libraries.discovery import (
    Library,
    catalog,
    format_libraries,
    global_catalog,
    global_library_root,
    installed_domains,
    library_records,
)
from clauz3.libraries.install import available_stdlib_tools, install_layer
from clauz3.libraries.layout import expand_import_roots
from clauz3.libraries.setup import choose_libraries
from clauz3.mcp import MCPClient
from clauz3.runner import discover_trusted_roots


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store = tmp_path / "user data"
    monkeypatch.setenv("CLAUZ3_HOME", str(store))
    monkeypatch.chdir(tmp_path)
    return store


def test_catalog_covers_stdlib_and_dependencies() -> None:
    bundled = {
        lib.source.removeprefix("stdlib:"): lib
        for lib in catalog()
        if lib.visibility == "bundled"
    }
    assert set(bundled) == set(available_stdlib_tools())
    for lib in bundled.values():
        assert lib.description
        assert lib.domains
        assert all(dep.removeprefix("stdlib:") in bundled for dep in lib.dependencies)
    # No bundled library declares a dependency; the mechanism is exercised
    # with the ``dependent_library`` fixture below.
    assert all(not lib.dependencies for lib in bundled.values())


def test_records_list_dependencies(tmp_path: Path, dependent_library: Library) -> None:
    (record,) = [
        r for r in library_records(tmp_path) if r["source"] == dependent_library.source
    ]
    assert record["dependencies"] == ["stdlib:filesystem"]
    assert "Library dependencies: stdlib:filesystem" in format_libraries([record])


def test_libraries_search_is_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_clone(source: str) -> Path:
        raise AssertionError("discovery must not clone repositories")

    monkeypatch.setattr("clauz3.libraries.install.resolve_remote_source", no_clone)
    assert main(["libraries", "AUTOLABS", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert len(records) == 1
    assert records[0]["source"] == "gh:normalform-ai/clauz3-tools-autolabs"
    assert records[0]["visibility"] == "repository"
    assert records[0]["domains"] == []
    assert "Review installation" in records[0]["description"]
    assert not records[0]["installed"]
    assert not list(tmp_path.iterdir())
    assert main(["libraries", "nothing-matches-this"]) == 0
    assert "No libraries match" in capsys.readouterr().out


def test_project_status_and_global_metadata_do_not_import_code(tmp_path: Path) -> None:
    trusted = tmp_path / "tools/git/trusted"
    trusted.mkdir(parents=True)
    (trusted / "__init__.py").write_text("raise AssertionError('must not import')")
    result = library_records(tmp_path, "stdlib:git")
    assert result[0]["installed"] is True
    assert not global_library_root().exists()


def test_dependency_install_preserves_existing_library(
    tmp_path: Path, dependent_library: Library
) -> None:
    assert main(["install", "stdlib:filesystem"]) == 0
    marker = tmp_path / ".clauz3/tools/filesystem/keep.txt"
    marker.write_text("operator customization")
    assert main(["install", dependent_library.source]) == 0
    assert marker.read_text() == "operator customization"
    assert installed_domains(tmp_path) == {"search", "filesystem"}


def test_dependency_install_pulls_in_dependency(
    tmp_path: Path, dependent_library: Library
) -> None:
    assert main(["install", dependent_library.source]) == 0
    assert installed_domains(tmp_path) == {"search", "filesystem"}


def test_global_install_requires_project_selection(
    tmp_path: Path,
    isolated_store: Path,
    capsys: pytest.CaptureFixture[str],
    dependent_library: Library,
) -> None:
    assert main(["install", "-g", dependent_library.source]) == 0
    assert "clauz3 install global:search" in capsys.readouterr().out
    assert installed_domains(isolated_store) == {"search", "filesystem"}
    assert not (tmp_path / ".clauz3/tools").exists()
    assert discover_trusted_roots(import_roots=expand_import_roots([tmp_path])) == []
    libraries = {lib.source: lib for lib in global_catalog()}
    assert libraries["global:search"].dependencies == ("global:filesystem",)
    assert libraries["global:filesystem"].origin == "stdlib:filesystem"
    assert main(["install", "global:search"]) == 0
    assert installed_domains(tmp_path) == {"search", "filesystem"}
    local = tmp_path / ".clauz3/tools/search/trusted/effects.py"
    saved = local.read_text()
    global_file = isolated_store / "tools/search/trusted/effects.py"
    global_file.write_text("changed global version")
    assert local.read_text() == saved
    assert not local.is_symlink()
    assert all(
        root.is_relative_to(tmp_path / ".clauz3/tools")
        for root in discover_trusted_roots(import_roots=expand_import_roots([tmp_path]))
    )
    case = tmp_path / "case.py"
    case.write_text(
        "import clauz3\n"
        "from tools.filesystem.trusted import contracts as fs\n"
        "from tools.search.trusted.effects import search\n"
        "@clauz3.guarantee(fs.only_read_under('/repo'))\n"
        "def main():\n"
        "    search('TODO', '/repo/a.txt')\n"
    )
    assert (
        main(
            [
                "prove",
                str(case),
                "--import-root",
                str(tmp_path / ".clauz3"),
                "--import-root",
                str(tmp_path),
                "--trusted-roots",
                str(tmp_path / ".clauz3/tools/search/trusted"),
                str(tmp_path / ".clauz3/tools/filesystem/trusted"),
            ]
        )
        == 0
    )


def test_global_copies_only_selected_domain(tmp_path: Path) -> None:
    assert main(["install", "--global", "stdlib:filesystem"]) == 0
    assert main(["install", "--global", "stdlib:git"]) == 0
    assert main(["install", "global:git"]) == 0
    assert installed_domains(tmp_path) == {"git"}


@pytest.mark.parametrize(
    "source", ["global:../git", "global:/tmp", "global:", "global:missing"]
)
def test_global_invalid_selection_cannot_escape(source: str, tmp_path: Path) -> None:
    assert main(["install", source]) == 2
    assert not (tmp_path / ".clauz3/tools").exists()


def test_global_into_conflict_and_self_copy_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["install", "-g", "--into", str(tmp_path), "stdlib:git"])
    assert main(["install", "-g", "stdlib:git"]) == 0
    assert main(["install", "-g", "global:git", "--force"]) == 2


def test_global_selection_rejects_symlinked_parent(
    tmp_path: Path, isolated_store: Path
) -> None:
    outside = tmp_path / "outside"
    (outside / "git/trusted").mkdir(parents=True)
    (outside / "git/trusted/effects.py").write_text("private contents")
    (isolated_store / "tools").mkdir(parents=True)
    (isolated_store / "tools/nested").symlink_to(outside, target_is_directory=True)
    assert main(["install", "global:nested/git"]) == 2
    assert not (tmp_path / ".clauz3/tools").exists()


def test_global_root_honors_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUZ3_HOME")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert global_library_root() == tmp_path / "data/clauz3"


def test_malformed_global_metadata_has_friendly_error(
    isolated_store: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["install", "-g", "stdlib:git"]) == 0
    metadata = isolated_store / "metadata/git/library.json"
    metadata.write_text("[]")
    assert main(["libraries"]) == 2
    assert "invalid library metadata" in capsys.readouterr().err


def test_default_setup_installs_file_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert choose_libraries(tmp_path)
    assert installed_domains(tmp_path) == {"filesystem"}
    assert not (tmp_path / ".clauz3/tools/git").exists()


@pytest.mark.parametrize("answer", ["q", "4"])
def test_setup_cancel_or_empty_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    assert choose_libraries(tmp_path) == (answer == "4")
    assert not (tmp_path / ".clauz3/tools").exists()


def test_setup_keeps_existing_project_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_layer("stdlib:filesystem", into=tmp_path)
    marker = tmp_path / ".clauz3/tools/filesystem/trusted/effects.py"
    marker.write_text("operator's existing library")
    monkeypatch.setattr("builtins.input", lambda prompt: "1")
    assert choose_libraries(tmp_path)
    assert marker.read_text() == "operator's existing library"


def test_setup_custom_selection_handles_bad_input_and_global(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert main(["install", "-g", "stdlib:git"]) == 0
    count = len([lib for lib in catalog() if lib.visibility == "bundled"])
    answers = iter(["bad", "3", "0", "9999", "oops", str(count + 1)])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert choose_libraries(tmp_path)
    assert installed_domains(tmp_path) == {"git"}


def test_empty_session_allows_libraries_but_refuses_run(tmp_path: Path) -> None:
    with MCPClient(
        command=sys.executable,
        args=["-P", "-m", "clauz3.sessions.tools", str(tmp_path)],
    ) as client:
        assert {tool["name"] for tool in client.list_tools()} == {
            "libraries",
            "tools",
            "run",
            "status",
            "list_skills",
            "read_skill",
        }
        result = client.call("libraries", query="stdlib:filesystem")
        assert "clauz3 install stdlib:filesystem" in result
        assert not (tmp_path / ".clauz3/tools").exists()
        # Installing during a running session must not silently expand its roots.
        install_layer("stdlib:filesystem", into=tmp_path)
        result = client.call("run", source="def main(): pass")
        assert "No project libraries selected" in result

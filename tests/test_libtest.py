import shutil
import sys
from contextlib import ExitStack
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture
from _pytest.monkeypatch import MonkeyPatch

from clauz3.cli import main
from clauz3.libtest import (
    LibTestError,
    _recipe_env,
    _resolve_tests_dir,
    run_lib_tests,
)

needs_just = pytest.mark.skipif(
    shutil.which("just") is None, reason="just is not installed"
)


def _write_lib(root: Path, justfile_body: str) -> Path:
    tests = root / "tests"
    tests.mkdir(parents=True)
    (tests / "Justfile").write_text(justfile_body)
    return root


def test_resolve_stdlib_tests_dir() -> None:
    with ExitStack() as stack:
        tests_dir = _resolve_tests_dir("stdlib:grep", stack)
        assert tests_dir.name == "tests"
        assert (tests_dir / "Justfile").is_file()
        assert (tests_dir / "cases").is_dir()


def test_resolve_bare_name_matches_stdlib() -> None:
    with ExitStack() as stack:
        tests_dir = _resolve_tests_dir("filesystem", stack)
        assert (tests_dir / "Justfile").is_file()


def test_resolve_tests_dir_directly(tmp_path: Path) -> None:
    lib = _write_lib(tmp_path, "test:\n    true\n")
    with ExitStack() as stack:
        assert _resolve_tests_dir(lib / "tests", stack) == lib / "tests"


def test_run_lib_tests_missing_just(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(LibTestError, match="was not found on PATH"):
        run_lib_tests("stdlib:grep")


def test_run_lib_tests_missing_source(tmp_path: Path) -> None:
    with pytest.raises(LibTestError, match="does not exist"):
        run_lib_tests(tmp_path / "nope")


def test_run_lib_tests_no_justfile(tmp_path: Path) -> None:
    empty = tmp_path / "lib"
    empty.mkdir()
    with pytest.raises(LibTestError, match="no tests/Justfile"):
        run_lib_tests(empty)


@needs_just
def test_run_lib_tests_passes(tmp_path: Path) -> None:
    lib = _write_lib(tmp_path, "test:\n    true\n")
    result = run_lib_tests(lib)
    assert result.returncode == 0
    assert result.tests_dir == lib / "tests"


@needs_just
def test_run_lib_tests_propagates_failure(tmp_path: Path) -> None:
    lib = _write_lib(tmp_path, "test:\n    exit 3\n")
    assert run_lib_tests(lib).returncode == 3


@needs_just
def test_cli_test(tmp_path: Path, capfd: CaptureFixture[str]) -> None:
    lib = _write_lib(tmp_path, "test:\n    echo ran\n")
    assert main(["test", str(lib)]) == 0
    assert "ran" in capfd.readouterr().out


@needs_just
def test_cli_test_custom_recipe(tmp_path: Path) -> None:
    lib = _write_lib(tmp_path, "check:\n    exit 4\n")
    assert main(["test", str(lib), "--recipe", "check"]) == 4


def test_cli_test_missing_source(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    assert main(["test", str(tmp_path / "nope")]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_recipe_env_points_at_current_interpreter() -> None:
    env = _recipe_env()
    assert env["CLAUZ3"] == f"{sys.executable} -m clauz3"
    assert env["DEAL"] == f"{sys.executable} -m deal"


@needs_just
def test_cli_test_runs_bundled_stdlib_without_uv() -> None:
    # Exercises the injected CLAUZ3 runner against a real bundled proof case,
    # so this passes even where `uv` is not on PATH.
    assert main(["test", "stdlib:grep", "--recipe", "only-pattern-pass"]) == 0

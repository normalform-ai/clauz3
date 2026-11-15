"""The non-proof checks of every example and bundled-library Justfile.

Each Justfile's ``test`` recipe lints its trusted effects, proves its cases
and, for some libraries, runs a runtime suite. ``just test`` no longer runs
those Justfiles: proof cases are pinned by the exact-conclusion tests
(``test_stdlib_case_conclusions.py``, ``test_example_provers.py``,
``test_email_prover.py``, ``test_bank_prover.py``), and the rest is here, so
each case is proved once and everything runs under pytest-xdist.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from clauz3.libtest import run_lib_tests
from clauz3.lint import lint_paths

ROOT = Path(__file__).resolve().parents[1]
JUSTFILES = sorted(
    [
        *ROOT.glob("examples/*/Justfile"),
        *ROOT.glob("src/clauz3/stdlib/*/tests/Justfile"),
    ]
)
EFFECTS = sorted(
    [
        *ROOT.glob("examples/*/tools/**/trusted/effects.py"),
        *ROOT.glob("src/clauz3/stdlib/*/tools/**/trusted/effects.py"),
    ]
)

#: Justfile recipes that check something other than proof cases.
RUNTIME_RECIPES = [
    ("db", "runtime-smoke"),
    ("google_workspace", "runtime"),
]

needs_just = pytest.mark.skipif(
    shutil.which("just") is None, reason="just is not installed"
)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _owner(justfile: Path) -> Path:
    """The example or library directory a Justfile tests."""
    return (
        justfile.parent.parent if justfile.parent.name == "tests" else justfile.parent
    )


@pytest.mark.parametrize("justfile", JUSTFILES, ids=_rel)
def test_every_justfile_lints_linted_effects(justfile: Path) -> None:
    # Each Justfile's ``lint`` recipe checks an effects module this file lints.
    assert any(_owner(justfile) in effects.parents for effects in EFFECTS)


@pytest.mark.parametrize("effects", EFFECTS, ids=_rel)
def test_trusted_effects_lint_clean(effects: Path) -> None:
    assert lint_paths([effects]) == []


@needs_just
@pytest.mark.parametrize("justfile", JUSTFILES, ids=_rel)
def test_justfile_test_recipe_parses(justfile: Path) -> None:
    """``clauz3 test`` users still run each Justfile; keep its recipes valid."""
    completed = subprocess.run(
        ["just", "-f", str(justfile), "--dry-run", "test"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@needs_just
@pytest.mark.parametrize(
    "library,recipe", RUNTIME_RECIPES, ids=[f"{lib}:{r}" for lib, r in RUNTIME_RECIPES]
)
def test_bundled_runtime_suite(library: str, recipe: str) -> None:
    assert run_lib_tests(f"stdlib:{library}", recipe=recipe).returncode == 0

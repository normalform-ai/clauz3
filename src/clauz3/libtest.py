"""Run a trusted library's bundled test suite.

``clauz3 test <source>`` resolves the same kinds of ``source`` as
``clauz3 install`` (a bundled stdlib tool, a local project path, or a folder)
and runs that library's ``tests/Justfile`` with ``just``.

The bundled libraries' tests use ``clauz3 prove``/``policy-check``, which are
purely static: the prover records each trusted-effect call as a fact and never
executes the function body, so running them has no real side effects. A library
whose tests instead use ``clauz3 run`` would execute real effects; such a
library should mock its effects before shipping run-based tests.
"""

import os
import shutil
import subprocess
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from importlib.resources import as_file
from pathlib import Path

from clauz3.install import (
    _stdlib_hint,
    _stdlib_name,
    _stdlib_root,
)
from clauz3.source import is_remote_source, resolve_remote_source

#: ``just`` recipe run by ``clauz3 test`` when none is requested.
DEFAULT_RECIPE = "test"


class LibTestError(Exception):
    """The test source is invalid or ``just`` is unavailable."""


@dataclass(frozen=True)
class LibTestResult:
    """Result of running a library's test suite."""

    tests_dir: Path
    recipe: str
    returncode: int


def run_lib_tests(
    source: str | Path,
    *,
    recipe: str = DEFAULT_RECIPE,
    just: str = "just",
) -> LibTestResult:
    """Run ``recipe`` from the ``tests/Justfile`` of the library ``source``.

    ``source`` is resolved like ``clauz3 install``: a bundled stdlib tool
    (``stdlib:<name>`` or a bare ``<name>``), a project directory containing a
    ``tests/`` folder, or a ``tests/`` folder itself.
    """

    if shutil.which(just) is None:
        raise LibTestError(
            f"{just!r} was not found on PATH; install it with "
            "`uv tool install rust-just`"
        )

    with ExitStack() as stack:
        tests_dir = _resolve_tests_dir(source, stack)
        justfile = tests_dir / "Justfile"
        completed = subprocess.run(
            [just, "-f", str(justfile), recipe],
            check=False,
            env=_recipe_env(),
        )
        return LibTestResult(
            tests_dir=tests_dir,
            recipe=recipe,
            returncode=completed.returncode,
        )


def _recipe_env() -> dict[str, str]:
    """Environment for the ``just`` subprocess.

    The bundled Justfiles read the ``clauz3``/``deal`` runner commands from
    ``CLAUZ3``/``DEAL``, defaulting to ``uv run`` for the source-checkout dev
    flow. Point them at the interpreter that is running ``clauz3 test`` so the
    suite also works from an installed wheel, where ``uv`` may be absent.
    """

    return {
        **os.environ,
        "CLAUZ3": f"{sys.executable} -m clauz3",
        "DEAL": f"{sys.executable} -m deal",
    }


def _resolve_tests_dir(source: str | Path, stack: ExitStack) -> Path:
    """Resolve ``source`` to a concrete ``tests/`` directory with a Justfile."""

    lib = _resolve_lib_dir(source, stack)
    if lib.name == "tests" and (lib / "Justfile").is_file():
        return lib
    tests = lib / "tests"
    if (tests / "Justfile").is_file():
        return tests
    raise LibTestError(f"no tests/Justfile found in {lib}")


def _resolve_lib_dir(source: str | Path, stack: ExitStack) -> Path:
    """Resolve ``source`` to the library directory that holds ``tests/``.

    stdlib tools are materialized to a real filesystem path so the bundled
    Justfile's relative paths (such as ``../tools``) resolve, the same way
    ``install`` materializes them for copying. Remote git sources
    (``gh:org/repo``, full URLs) are cloned into a content-addressed cache
    and then resolved like a local path.
    """

    stdlib_name = _stdlib_name(source)
    if stdlib_name is not None:
        lib = _stdlib_root() / stdlib_name
        if not lib.is_dir():
            raise LibTestError(f"unknown stdlib tool: {stdlib_name}{_stdlib_hint()}")
        return stack.enter_context(as_file(lib))

    if is_remote_source(source):
        path = resolve_remote_source(str(source))
    else:
        path = Path(source)
    if not path.exists():
        raise LibTestError(f"source path does not exist: {path}{_stdlib_hint()}")
    return path

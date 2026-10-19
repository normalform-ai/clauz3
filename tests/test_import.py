import subprocess
import sys
from importlib.metadata import version

import clauz3


def test_version_comes_from_package_metadata() -> None:
    """The version is set from the release tag at build time, not by hand."""
    assert clauz3.__version__ == version("clauz3")


def test_cli_reports_package_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "clauz3", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == f"clauz3 {clauz3.__version__}"

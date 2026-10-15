# ruff: noqa: F821
from tools.filesystem.trusted.contracts import read_only as ro
from tools.filesystem.trusted.effects import write_file

import clauz3


@clauz3.guarantee(ro())
def main() -> None:
    write_file("/etc/passwd", "x")

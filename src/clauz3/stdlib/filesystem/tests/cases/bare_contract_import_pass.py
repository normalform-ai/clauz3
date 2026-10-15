# ruff: noqa: F821
from tools.filesystem.trusted.contracts import read_only
from tools.filesystem.trusted.effects import read_file

import clauz3


@clauz3.guarantee(read_only())
def main() -> None:
    read_file("/repo/a.py")
    read_file("/repo/b.py")

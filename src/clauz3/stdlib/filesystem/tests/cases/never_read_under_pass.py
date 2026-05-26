# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file

import clauz3


@clauz3.guarantee(fs.never_read_under("/home/user/.ssh"))
def main() -> None:
    read_file("/repo/README.md")

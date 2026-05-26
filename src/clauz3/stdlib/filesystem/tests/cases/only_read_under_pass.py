# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file

import clauz3


@clauz3.guarantee(fs.only_read_under("/repo"))
@clauz3.guarantee(fs.read_only())
def main() -> None:
    read_file("/repo/src/app.py")

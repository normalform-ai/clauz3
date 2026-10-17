# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import move

import clauz3


@clauz3.guarantee(fs.only_write_under("/repo"))
def main() -> None:
    move("/repo/build/old.log", "/tmp/old.log")

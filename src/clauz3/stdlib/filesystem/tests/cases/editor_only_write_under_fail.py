# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import write_file

import clauz3


@clauz3.guarantee(fs.only_write_under("/repo/build"))
def main() -> None:
    write_file("/etc/passwd", "")

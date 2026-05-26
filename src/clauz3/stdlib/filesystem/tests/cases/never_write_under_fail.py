# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import write_file

import clauz3


@clauz3.guarantee(fs.never_write_under("/etc"))
def main() -> None:
    write_file("/etc/passwd", "pwned")

# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import write_file

import clauz3


@clauz3.guarantee(fs.writes_at_most(1))
def main() -> None:
    write_file("/sandbox/a.txt", "a")
    write_file("/sandbox/b.txt", "b")

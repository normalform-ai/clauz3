# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import write_file

import clauz3


@clauz3.guarantee(fs.must_not_write("BEGIN PRIVATE KEY"))
def main() -> None:
    write_file("/tmp/leak", "BEGIN PRIVATE KEY\n...\n")

# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import write_file

import clauz3


@clauz3.guarantee(fs.write_length_at_most(8))
def main() -> None:
    write_file("/tmp/a", "this is much longer than eight characters")

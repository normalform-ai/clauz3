"""never_write_under(".") covers bare relative paths, not only ./ ones."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import write_file

import clauz3


@clauz3.guarantee(fs.never_write_under("."))
def main() -> None:
    write_file("notes.txt", "x")

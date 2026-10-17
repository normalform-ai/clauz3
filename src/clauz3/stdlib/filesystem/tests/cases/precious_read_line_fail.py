"""read_line reads the file, so the read policy covers it."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_line

import clauz3


@clauz3.guarantee(fs.never_read_under("/repo/PRECIOUS.md"))
def main() -> None:
    print(read_line("/repo/PRECIOUS.md", 1))

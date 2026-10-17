"""A file moved out from under a read policy cannot be read under its new name.

move reads its source, so the move itself is refused.
"""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import move, read_file

import clauz3


@clauz3.guarantee(fs.never_read_under("/repo/PRECIOUS.md"))
def main() -> None:
    move("/repo/PRECIOUS.md", "/tmp/innocent.txt")
    print(read_file("/tmp/innocent.txt"))

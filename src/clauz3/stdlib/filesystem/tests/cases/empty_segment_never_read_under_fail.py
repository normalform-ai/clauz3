"""An empty segment cannot step around a deny-list prefix: the path is refused."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file

import clauz3


@clauz3.guarantee(fs.never_read_under("/repo/PRECIOUS.md"))
def main() -> None:
    print(read_file("/repo//PRECIOUS.md"))

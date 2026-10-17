"""grep reads the file it searches, so the read policy covers it."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import grep, read_file

import clauz3


@clauz3.guarantee(fs.never_read_under("/repo/PRECIOUS.md"))
def main() -> None:
    print(read_file("/repo/README.md"))
    print(grep("password", "/repo/PRECIOUS.md"))

"""One read policy covers every tool that reads: read_file, read_line, grep, copy."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import copy, grep, read_file, read_line

import clauz3


@clauz3.guarantee(fs.never_read_under("/repo/PRECIOUS.md"))
def main() -> None:
    print(read_file("/repo/README.md"))
    print(read_line("/repo/NOTES.md", 1))
    print(grep("TODO", "/repo/src/app.py"))
    copy("/repo/src/app.py", "/tmp/app.py")

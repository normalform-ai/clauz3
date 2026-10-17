"""A sibling whose name is a prefix of the protected file is not its directory."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import write_file

import clauz3


@clauz3.guarantee(fs.never_write_under("/repo/notes.md.orig"))
def main() -> None:
    write_file("/repo/notes.md", "x")

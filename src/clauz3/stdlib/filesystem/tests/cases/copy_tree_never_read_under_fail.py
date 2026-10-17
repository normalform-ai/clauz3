"""Copying a directory reads everything under it, including the protected file."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import copy

import clauz3


@clauz3.guarantee(fs.never_read_under("/repo/PRECIOUS.md"))
def main() -> None:
    copy("/repo", "/tmp/repo-backup")

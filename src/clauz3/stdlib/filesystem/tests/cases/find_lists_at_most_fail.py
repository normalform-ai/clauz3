# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import find

import clauz3


@clauz3.guarantee(fs.lists_at_most(2))
def main() -> None:
    find("/repo/src", ["-name", "*.py"])
    find("/repo/tests", ["-name", "*.py"])
    find("/repo/docs", ["-name", "*.md"])

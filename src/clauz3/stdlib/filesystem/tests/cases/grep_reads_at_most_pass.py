# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import grep

import clauz3


@clauz3.guarantee(fs.reads_at_most(2))
def main() -> None:
    grep("TODO", "/repo/a.py")
    grep("FIXME", "/repo/b.py")

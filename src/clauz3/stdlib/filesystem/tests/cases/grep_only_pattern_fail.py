# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import grep

import clauz3


@clauz3.guarantee(fs.only_pattern("TODO"))
def main() -> None:
    grep("FIXME", "/repo/a.py")

# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import touch

import clauz3


@clauz3.guarantee(fs.no_chmods())
def main() -> None:
    touch("/repo/build/marker")

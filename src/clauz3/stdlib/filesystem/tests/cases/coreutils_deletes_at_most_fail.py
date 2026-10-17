# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import remove

import clauz3


@clauz3.guarantee(fs.deletes_at_most(2))
def main() -> None:
    remove("/repo/build/a")
    remove("/repo/build/b")
    remove("/repo/build/c")

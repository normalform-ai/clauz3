# ruff: noqa: F821
from tools.coreutils.trusted import contracts as cu
from tools.coreutils.trusted.effects import remove

import clauz3


@clauz3.guarantee(cu.deletes_at_most(2))
def main() -> None:
    remove("/repo/build/a")
    remove("/repo/build/b")
    remove("/repo/build/c")

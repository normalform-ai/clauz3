# ruff: noqa: F821
from tools.coreutils.trusted import contracts as cu
from tools.coreutils.trusted.effects import remove

import clauz3


@clauz3.guarantee(cu.never_delete_under("/etc"))
def main() -> None:
    remove("/repo/build/cache")

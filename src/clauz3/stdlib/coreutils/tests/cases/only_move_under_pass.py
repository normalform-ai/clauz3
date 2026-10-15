# ruff: noqa: F821
from tools.coreutils.trusted import contracts as cu
from tools.coreutils.trusted.effects import move

import clauz3


@clauz3.guarantee(cu.only_move_under("/repo/archive"))
def main() -> None:
    move("/repo/build/old.log", "/repo/archive/old.log")

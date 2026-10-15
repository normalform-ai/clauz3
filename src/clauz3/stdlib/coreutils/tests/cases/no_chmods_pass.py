# ruff: noqa: F821
from tools.coreutils.trusted import contracts as cu
from tools.coreutils.trusted.effects import touch

import clauz3


@clauz3.guarantee(cu.no_chmods())
def main() -> None:
    touch("/repo/build/marker")

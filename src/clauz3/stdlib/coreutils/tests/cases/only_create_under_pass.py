# ruff: noqa: F821
from tools.coreutils.trusted import contracts as cu
from tools.coreutils.trusted.effects import make_dir, touch

import clauz3


@clauz3.guarantee(cu.only_create_under("/repo/build"))
def main() -> None:
    make_dir("/repo/build/out")
    touch("/repo/build/out/.keep")

# ruff: noqa: F821
from tools.coreutils.trusted import contracts as cu
from tools.coreutils.trusted.effects import copy

import clauz3


@clauz3.guarantee(cu.no_deletes())
def main() -> None:
    copy("/repo/src/app.py", "/repo/build/app.py")

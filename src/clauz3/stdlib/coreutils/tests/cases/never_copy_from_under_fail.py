# ruff: noqa: F821
from tools.coreutils.trusted import contracts as cu
from tools.coreutils.trusted.effects import copy

import clauz3


@clauz3.guarantee(cu.never_copy_from_under("/home/user/.ssh"))
def main() -> None:
    copy("/home/user/.ssh/id_rsa", "/repo/build/leak")

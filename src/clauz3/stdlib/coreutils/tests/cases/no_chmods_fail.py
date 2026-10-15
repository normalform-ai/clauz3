# ruff: noqa: F821
from tools.coreutils.trusted import contracts as cu
from tools.coreutils.trusted.effects import chmod

import clauz3


@clauz3.guarantee(cu.no_chmods())
def main() -> None:
    chmod("/repo/build/run.sh", 0o755)

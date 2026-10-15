# ruff: noqa: F821
from tools.coreutils.trusted import contracts as cu
from tools.coreutils.trusted.effects import chmod

import clauz3


@clauz3.guarantee(cu.only_modes([0o644, 0o755, 0o600, 0o700]))
def main() -> None:
    chmod("/repo/build/run.sh", 0o755)

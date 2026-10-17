# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import chmod

import clauz3


@clauz3.guarantee(fs.no_chmods())
def main() -> None:
    chmod("/repo/build/run.sh", 0o755)

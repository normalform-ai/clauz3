# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import chmod

import clauz3


@clauz3.guarantee(fs.only_modes([0o644, 0o755, 0o600, 0o700]))
def main() -> None:
    chmod("/repo/build/run.sh", 0o755)

"""chmod modifies the filesystem, so read_only forbids it."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import chmod

import clauz3


@clauz3.guarantee(fs.read_only())
def main() -> None:
    chmod("/repo/run.sh", 0o755)

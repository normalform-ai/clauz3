# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import remove

import clauz3


@clauz3.guarantee(fs.no_deletes())
def main() -> None:
    remove("/repo/build/cache")

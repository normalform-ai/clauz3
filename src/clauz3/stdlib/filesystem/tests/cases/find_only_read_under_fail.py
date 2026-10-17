# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import find

import clauz3


@clauz3.guarantee(fs.only_read_under("/repo"))
def main() -> None:
    find("/etc", ["-name", "*.conf"])

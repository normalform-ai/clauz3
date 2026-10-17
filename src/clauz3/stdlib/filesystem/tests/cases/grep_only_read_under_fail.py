# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import grep

import clauz3


@clauz3.guarantee(fs.only_read_under("/repo"))
def main() -> None:
    grep("secret", "/etc/shadow")

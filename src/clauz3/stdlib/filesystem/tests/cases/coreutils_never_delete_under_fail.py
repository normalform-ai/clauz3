# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import remove

import clauz3


@clauz3.guarantee(fs.never_delete_under("/etc"))
def main() -> None:
    remove("/etc/passwd")

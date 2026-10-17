"""A "." segment cannot step around a deny-list prefix: the path is refused."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import remove

import clauz3


@clauz3.guarantee(fs.never_delete_under("/home/user/.ssh"))
def main() -> None:
    remove("/home/user/./.ssh")

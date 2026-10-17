"""A relative path may reach an absolute prefix from the working directory."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import remove

import clauz3


@clauz3.guarantee(fs.never_delete_under("/home/user/.ssh"))
def main() -> None:
    remove(".")

"""Removing ./data removes data."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import remove

import clauz3


@clauz3.guarantee(fs.never_delete_under("data"))
def main() -> None:
    remove("./data")

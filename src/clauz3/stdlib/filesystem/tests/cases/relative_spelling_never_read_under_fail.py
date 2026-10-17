"""A prefix written with ./ also protects the bare spelling."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file

import clauz3


@clauz3.guarantee(fs.never_read_under("./data/.env"))
def main() -> None:
    print(read_file("data/.env"))

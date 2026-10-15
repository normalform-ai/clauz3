from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file

import clauz3


@clauz3.guarantee(fs.only_read_under("/repo/"))
def main() -> None:
    print(read_file("/repo/input.txt").count("\n"))

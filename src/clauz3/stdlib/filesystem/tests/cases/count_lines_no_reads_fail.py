from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_lines

import clauz3


@clauz3.guarantee(fs.no_reads())
def main() -> None:
    print(len(read_lines("/repo/input.txt")))

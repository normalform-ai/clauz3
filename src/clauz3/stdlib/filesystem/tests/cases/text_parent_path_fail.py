from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_line

import clauz3


@clauz3.guarantee(fs.no_guarantees())
def main() -> None:
    read_line("input/../private.txt", 1)

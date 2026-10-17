from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_line

import clauz3


@clauz3.guarantee(fs.only_read_under("input/"))
def main() -> None:
    read_line("input/message.txt", 1)

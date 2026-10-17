from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_line

import clauz3


@clauz3.guarantee(fs.never_read_under("private/"))
def main() -> None:
    read_line("private/message.txt", 1)

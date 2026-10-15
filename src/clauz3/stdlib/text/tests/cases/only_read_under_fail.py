from tools.text.trusted import contracts as text
from tools.text.trusted.effects import read_line

import clauz3


@clauz3.guarantee(text.only_read_under("input/"))
def main() -> None:
    read_line("private/message.txt", 1)

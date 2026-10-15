from tools.text.trusted import contracts as text
from tools.text.trusted.effects import read_line

import clauz3


@clauz3.guarantee(text.reads_at_most(1))
def main() -> None:
    read_line("input/message.txt", 1)
    read_line("input/message.txt", 2)

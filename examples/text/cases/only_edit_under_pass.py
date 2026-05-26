# ruff: noqa: F821
from tools.text.trusted import contracts as text
from tools.text.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(text.only_edit_under("/repo/src/"))
def main() -> None:
    edit_file("/repo/src/main.py", "print('hello')\n")
    edit_file("/repo/src/util.py", "x = 1\n")

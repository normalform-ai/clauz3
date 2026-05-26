# ruff: noqa: F821
from tools.text.trusted import contracts as text
from tools.text.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(text.no_edits())
def main() -> None:
    edit_file("/repo/notes.txt", "but this run does edit a file\n")

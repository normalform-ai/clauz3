# ruff: noqa: F821
from tools.text.trusted import contracts as text
from tools.text.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(text.edit_length_at_most(40))
def main() -> None:
    edit_file(
        "/repo/notes.txt",
        "this replacement body is well beyond the forty character limit",
    )

# ruff: noqa: F821
from tools.editor.trusted import contracts as ed
from tools.editor.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(ed.replace_length_at_most(32))
def main() -> None:
    edit_file("/tmp/a", "short content")

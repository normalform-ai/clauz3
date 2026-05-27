# ruff: noqa: F821
from tools.editor.trusted import contracts as ed
from tools.editor.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(ed.edits_at_most(1))
def main() -> None:
    edit_file("/tmp/a", "x")
    edit_file("/tmp/b", "y")

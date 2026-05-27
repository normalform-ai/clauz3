# ruff: noqa: F821
from tools.editor.trusted import contracts as ed
from tools.editor.trusted.effects import append_file, edit_file

import clauz3


@clauz3.guarantee(ed.edits_at_most(2))
def main() -> None:
    edit_file("/tmp/a", "x")
    append_file("/tmp/a", "y")

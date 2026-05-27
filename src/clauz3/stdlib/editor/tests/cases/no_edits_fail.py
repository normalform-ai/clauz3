# ruff: noqa: F821
from tools.editor.trusted import contracts as ed
from tools.editor.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(ed.no_edits())
def main() -> None:
    edit_file("/tmp/scratch", "anything")

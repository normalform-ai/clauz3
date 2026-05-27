# ruff: noqa: F821
from tools.editor.trusted import contracts as ed

import clauz3


@clauz3.guarantee(ed.no_edits())
def main() -> None:
    pass

# ruff: noqa: F821
from tools.editor.trusted import contracts as ed
from tools.editor.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(ed.never_edit_under("/etc"))
def main() -> None:
    edit_file("/etc/hosts", "127.0.0.1 evil.example.com\n")

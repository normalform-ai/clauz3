# ruff: noqa: F821
from tools.editor.trusted import contracts as ed
from tools.editor.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(ed.must_not_replace("BEGIN PRIVATE KEY"))
def main() -> None:
    edit_file("/repo/build/cert.pem", "BEGIN CERTIFICATE\n...\nEND CERTIFICATE\n")

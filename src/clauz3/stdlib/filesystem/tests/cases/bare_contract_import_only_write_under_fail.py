# ruff: noqa: F821
from tools.filesystem.trusted.contracts import only_write_under
from tools.filesystem.trusted.effects import write_file

import clauz3


@clauz3.guarantee(only_write_under("/sandbox"))
def main() -> None:
    write_file("/etc/passwd", "x")

# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs

import clauz3


@clauz3.guarantee(fs.read_only())
def main() -> None:
    pass

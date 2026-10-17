# ruff: noqa: F821
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import make_dir, touch

import clauz3


@clauz3.guarantee(fs.only_write_under("/repo/build"))
def main() -> None:
    make_dir("/repo/build/out")
    touch("/repo/build/out/.keep")

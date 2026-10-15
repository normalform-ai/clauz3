from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file, write_file

import clauz3


@clauz3.guarantee(fs.read_only())
def main() -> None:
    if read_file("/repo/input.txt").count("\n") > 0:
        write_file("/repo/out.txt", "bad")

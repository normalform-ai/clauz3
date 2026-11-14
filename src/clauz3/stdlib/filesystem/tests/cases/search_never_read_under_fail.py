from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import search_files

import clauz3


@clauz3.guarantee(fs.never_read_under("/home/user/.ssh"))
def main() -> None:
    search_files("BEGIN", "/home/user/.ssh", "", 20)

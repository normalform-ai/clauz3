from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import move

import clauz3


@clauz3.guarantee(fs.only_writes_files_under(clauz3.DirectoryPath("data")))
def main() -> None:
    move("data/source", "data/destination")

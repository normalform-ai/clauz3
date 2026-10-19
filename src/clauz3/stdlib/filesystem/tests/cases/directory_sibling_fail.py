from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file

import clauz3


@clauz3.guarantee(fs.only_reads_files_under(clauz3.DirectoryPath("data")))
def main() -> None:
    print(read_file("database/input.txt"))

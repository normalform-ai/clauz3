from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file

import clauz3


@clauz3.guarantee(fs.only_reads_files([clauz3.FilePath("README.md")]))
def main() -> None:
    print(read_file("./README.md"))

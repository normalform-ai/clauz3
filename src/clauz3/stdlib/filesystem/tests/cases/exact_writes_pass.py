from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import write_file

import clauz3


@clauz3.guarantee(fs.only_writes_files([clauz3.FilePath("test.md")]))
@clauz3.guarantee(fs.no_reads())
def main() -> None:
    write_file("test.md", "hello")

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(fs.only_writes_files_under(clauz3.DirectoryPath("src")))
def main() -> None:
    edit_file("src/app.py", "DEBUG = True", "DEBUG = False")
    edit_file("setup.cfg", "a", "b")

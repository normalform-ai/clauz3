from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file, search_files

import clauz3


@clauz3.guarantee(fs.only_writes_files_under(clauz3.DirectoryPath("src")))
@clauz3.guarantee(fs.writes_at_most(9))
def main() -> None:
    for match in search_files("old_name", "src", "*.py", 10):
        print(match.path, match.line_number)
        edit_file("src/app.py", "old_name", "new_name")

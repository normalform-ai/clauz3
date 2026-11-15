from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import search_files

import clauz3


@clauz3.guarantee(fs.only_reads_files([clauz3.FilePath("src/app.py")]))
def main() -> None:
    for match in search_files("TODO", "src/app.py", "", 20):
        print(match.path, match.line_number, match.text)

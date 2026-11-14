from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import search_files

import clauz3


@clauz3.guarantee(fs.never_read_under("/home/user/.ssh"))
def main() -> None:
    for match in search_files("TODO", "/repo/src", "*.py", 20):
        print(match.path, match.line_number, match.text)

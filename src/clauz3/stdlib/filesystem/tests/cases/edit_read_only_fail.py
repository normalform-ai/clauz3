from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(fs.read_only())
def main() -> None:
    edit_file("src/app.py", "a", "b")

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(fs.must_not_write("SECRET"))
def main() -> None:
    edit_file("src/app.py", "DEBUG", "SECRET")

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(fs.write_length_at_most(8))
def main() -> None:
    edit_file("src/app.py", "x", "this is much longer than eight characters")

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


# Only the inserted text is checked: removing "SECRET" is allowed.
@clauz3.guarantee(fs.must_not_write("SECRET"))
def main() -> None:
    edit_file("src/app.py", "SECRET", "REDACTED")

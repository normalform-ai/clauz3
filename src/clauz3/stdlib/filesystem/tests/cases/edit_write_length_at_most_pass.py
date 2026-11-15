from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


# Only the inserted text is measured, not the old text or the resulting file.
@clauz3.guarantee(fs.write_length_at_most(8))
def main() -> None:
    edit_file("src/app.py", "this is much longer than eight characters", "short")

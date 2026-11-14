from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(fs.never_read_under("/home/user/.ssh"))
def main() -> None:
    edit_file("/home/user/.ssh/config", "Host a", "Host b")

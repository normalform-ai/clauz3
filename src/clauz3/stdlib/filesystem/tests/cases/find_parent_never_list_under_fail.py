"""Walking a directory above the prefix lists its names too."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import find

import clauz3


@clauz3.guarantee(fs.never_list_under("/home/user/.ssh"))
def main() -> None:
    print(find("/home/user", ["-type", "f"]))

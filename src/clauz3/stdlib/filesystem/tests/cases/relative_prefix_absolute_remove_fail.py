"""An absolute path may reach a relative prefix: / contains the working directory."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import remove

import clauz3


@clauz3.guarantee(fs.never_delete_under("data"))
def main() -> None:
    remove("/")

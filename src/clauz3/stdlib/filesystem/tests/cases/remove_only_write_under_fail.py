"""A delete is a write: only_write_under confines removals too."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import remove, write_file

import clauz3


@clauz3.guarantee(fs.only_write_under("/sandbox"))
def main() -> None:
    write_file("/sandbox/out.txt", "ok")
    remove("/etc/passwd")

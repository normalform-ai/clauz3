"""must_not_write covers appended text as well as whole-file writes."""

from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import append_file

import clauz3


@clauz3.guarantee(fs.must_not_write("BEGIN PRIVATE KEY"))
def main() -> None:
    append_file("/tmp/log.txt", "BEGIN PRIVATE KEY\n")

"""Line-oriented UTF-8 file reads for restricted agent programs."""

from pathlib import Path

import deal

import clauz3
from clauz3._path_safety import has_no_parent_segments


@clauz3.requires(
    lambda path, line_number: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(
    lambda path, line_number: len(path) >= 1 and line_number >= 1,
    message="path must be non-empty and line_number must be positive",
)
@deal.has("read")
@clauz3.trusted
def read_line(path: str, line_number: int) -> str:
    """Read a one-based line, without its terminator; return empty at EOF.

    Decode UTF-8 with universal newline handling. Preserve all other whitespace.
    """
    with Path(path).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            if number == line_number:
                return line.removesuffix("\n")
    return ""

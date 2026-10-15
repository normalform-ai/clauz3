"""Trusted filesystem effects.

These are real (non-mock) side-effecting functions. The prover never executes
their bodies: it records each call as an effect fact and proves the declared
preconditions. The bodies run only under ``clauz3 run``, after a program has
been proved and approved.

The contracts in ``contracts.py`` constrain *where* on the filesystem a
program may read or write, and expose their path rule as ``reads_under`` so a
layer whose effect is also a read (``grep``, ``find``) can apply it to its
own.
"""

from pathlib import Path

import deal

import clauz3
from clauz3._path_safety import has_no_parent_segments


@clauz3.requires(
    lambda path: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("read")
@clauz3.trusted
def read_file(path: str) -> str:
    """Read and return the UTF-8 text contents of ``path``."""
    return Path(path).read_text(encoding="utf-8")


@clauz3.requires(
    lambda path: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("read")
@clauz3.trusted
def read_lines(path: str) -> list[str]:
    """Read UTF-8 logical lines without terminators, using str.splitlines.

    Empty files return []; blank lines are retained; a final unterminated
    line counts. This is not wc -l, which counts LF bytes. Decode and I/O
    errors propagate, as with read_file.
    """
    return Path(path).read_text(encoding="utf-8").splitlines()


@clauz3.requires(
    lambda path, content: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(lambda path, content: len(path) > 0, message="path must be non-empty")
@deal.has("write")
@clauz3.trusted
def write_file(path: str, content: str) -> None:
    """Write ``content`` to ``path`` as UTF-8 text, creating parent dirs."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

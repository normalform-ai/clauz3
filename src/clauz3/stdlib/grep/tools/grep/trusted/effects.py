"""Trusted grep effect: a real (non-mock) substitute for an agent's ripgrep.

``grep`` reads a file, so ``contracts.py`` applies the filesystem layer's
path rule to this effect. That means the
filesystem read policies (``only_read_under``, ``never_read_under``, ...) apply
to grep calls in exactly the same way they apply to ``read_file``. The prover
records each call as a fact; the body runs only under ``clauz3 run``.
"""

from pathlib import Path

import deal

import clauz3
from clauz3._path_safety import has_no_parent_segments


@clauz3.requires(
    lambda pattern, path: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(
    lambda pattern, path: len(pattern) >= 1 and len(path) >= 1,
    message="pattern and path must be non-empty",
)
@deal.has("read")
@clauz3.trusted
def grep(pattern: str, path: str) -> list[str]:
    """Return the lines in ``path`` that contain the substring ``pattern``."""
    text = Path(path).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if pattern in line]

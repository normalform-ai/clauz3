"""Trusted grep effect: a real (non-mock) substitute for an agent's ripgrep.

``grep`` reads a file, so it carries deal's ``read`` marker. That means the
filesystem read policies (``only_read_under``, ``never_read_under``, ...) apply
to grep calls in exactly the same way they apply to ``read_file``. The prover
records each call as a fact; the body runs only under ``clauz3 run``.
"""

from pathlib import Path

import deal


@deal.pre(
    lambda pattern, path: len(pattern) >= 1 and len(path) >= 1,
    message="pattern and path must be non-empty",
)
@deal.has("read", "trusted")
def grep(pattern: str, path: str) -> list[str]:
    """Return the lines in ``path`` that contain the substring ``pattern``."""
    text = Path(path).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if pattern in line]

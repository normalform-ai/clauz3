"""Trusted filesystem effects.

These are real (non-mock) side-effecting functions. The prover never executes
their bodies: it records each call as an effect fact and proves the declared
preconditions. The bodies run only under ``clauz3 run``, after a program has
been proved and approved.

Reads carry deal's ``read`` marker and writes carry the ``write`` marker, so
the contracts in ``contracts.py`` can constrain *where* on the filesystem a
program may read or write.
"""

from pathlib import Path

import deal


@deal.pre(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("read", "trusted")
def read_file(path: str) -> str:
    """Read and return the UTF-8 text contents of ``path``."""
    return Path(path).read_text(encoding="utf-8")


@deal.pre(lambda path, content: len(path) > 0, message="path must be non-empty")
@deal.has("write", "trusted")
def write_file(path: str, content: str) -> None:
    """Write ``content`` to ``path`` as UTF-8 text, creating parent dirs."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

"""Trusted coreutils effects: real (non-mock) bash-level file mutations.

Where ``filesystem`` reads and writes file *contents*, ``coreutils`` covers the
destructive bash primitives an agent reaches for — ``rm``, ``mv``, ``cp``,
``mkdir``, ``touch``, ``chmod`` — whose blast radius lives entirely in their
path arguments and is therefore provable up front.

The bodies use ``pathlib``/``shutil``/``os`` directly (the same grain as
``filesystem``), not a shell, so no argument is ever interpreted by a shell.
The prover never runs the bodies — it records each call as a fact and proves
the contract; the bodies run only under ``clauz3 run``.

The relations in ``contracts.py`` group them by the fields they carry, which
is what a policy can actually say something about:

- ``remove`` alone (field ``path``), because deletion is the destructive one.
- ``make_dir`` and ``touch`` together (field ``path``), the creating pair.
- ``move`` and ``copy`` separately (fields ``src`` and ``dest``), so a
  destination can be pinned independently of its source.
- ``chmod`` alone (fields ``path`` and ``mode``).

The ``@deal.has(...)`` markers alongside are deal's own: they buy its runtime
guard and the ``clauz3 lint`` check on these bodies, and play no part in what a
contract can say.
"""

import os
import shutil
from pathlib import Path

import deal

import clauz3
from clauz3._path_safety import has_no_parent_segments


@clauz3.requires(
    lambda path: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("io")
@clauz3.trusted
def remove(path: str) -> None:
    """Delete ``path`` — a file, or a directory and everything under it.

    Recorded as the ``remove`` effect so ``only_delete_under`` /
    ``never_delete_under`` can bound *where* a program may delete.
    """
    target = Path(path)
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)


@clauz3.requires(
    lambda path: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("write", "io")
@clauz3.trusted
def make_dir(path: str) -> None:
    """Create directory ``path`` (with parents). Recorded under ``write``."""
    Path(path).mkdir(parents=True, exist_ok=True)


@clauz3.requires(
    lambda path: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("write", "io")
@clauz3.trusted
def touch(path: str) -> None:
    """Create empty file ``path`` (or bump its mtime). Recorded under ``write``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()


@clauz3.requires(
    lambda src, dest: has_no_parent_segments(src) and has_no_parent_segments(dest),
    message="src and dest must not contain parent-directory segments",
)
@clauz3.requires(
    lambda src, dest: len(src) > 0 and len(dest) > 0,
    message="src and dest must be non-empty",
)
@deal.has("io")
@clauz3.trusted
def move(src: str, dest: str) -> None:
    """Move ``src`` to ``dest``.

    Because a move both removes its source and writes its destination, the
    ``move`` relation carries both ``src`` and ``dest`` fields; ``no_deletes``
    governs only ``remove`` — use ``no_moves`` / ``only_move_*`` for moves.
    """
    shutil.move(src, dest)


@clauz3.requires(
    lambda src, dest: has_no_parent_segments(src) and has_no_parent_segments(dest),
    message="src and dest must not contain parent-directory segments",
)
@clauz3.requires(
    lambda src, dest: len(src) > 0 and len(dest) > 0,
    message="src and dest must be non-empty",
)
@deal.has("io")
@clauz3.trusted
def copy(src: str, dest: str) -> None:
    """Copy ``src`` to ``dest`` (file or directory tree). Recorded under ``copy``."""
    if Path(src).is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


@clauz3.requires(
    lambda path, mode: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(lambda path, mode: len(path) > 0, message="path must be non-empty")
@deal.has("io")
@clauz3.trusted
def chmod(path: str, mode: int) -> None:
    """Set the permission bits of ``path`` to ``mode``.

    Recorded with ``path`` and ``mode`` fields, so a
    program can prove it only ever sets modes from a vetted allowlist (e.g. no
    world-writable or setuid bits) and only under a permitted subtree.
    """
    os.chmod(path, mode)

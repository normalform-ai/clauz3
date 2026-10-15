"""Trusted rsync effect: a real (non-mock) file-sync subprocess.

``rsync`` copies ``src`` to ``dest`` with a ``delete`` flag and extra ``args``.
``src``, ``dest``, and ``delete`` are first-class parameters rather than buried
in an opaque flag list precisely so the contracts in ``contracts.py`` can prove
*where* a sync may write (``only_dest_under``) and that it will not delete
extraneous destination files (``no_delete``) — the two genuinely dangerous
properties of an agent-issued rsync.

A sync writes, but its contracts are its own: ``dest`` and ``src`` are two
paths, which the single-path filesystem write policies cannot describe.

The body shells out via ``subprocess.run`` with an explicit argument vector
(never a shell string), so ``syscall`` is declared among its deal markers. The
prover never runs the body — it records each call as a fact and proves the
contract; the body runs only under ``clauz3 run``.
"""

import subprocess

import deal

import clauz3
from clauz3._path_safety import has_no_parent_segments

DEFAULT_TIMEOUT_SECONDS = 600


@clauz3.requires(
    lambda src, dest, delete, args: (
        has_no_parent_segments(src) and has_no_parent_segments(dest)
    ),
    message="src and dest must not contain parent-directory segments",
)
@clauz3.requires(
    lambda src, dest, delete, args: len(src) > 0 and len(dest) > 0,
    message="src and dest must be non-empty",
)
@deal.has("write", "io", "network", "syscall")
@clauz3.trusted
def rsync(src: str, dest: str, delete: bool, args: list[str]) -> str:
    """Run ``rsync`` from ``src`` to ``dest`` and return its stdout.

    ``delete`` adds ``--delete`` (remove destination files with no source
    counterpart); ``contracts.no_delete()`` forbids it. ``args`` carries any
    further flags as an explicit vector, so no field is interpreted by a shell.
    Where the sync may write (``only_dest_under``) is an agent-stated guarantee
    recorded as the ``rsync`` effect.
    """
    flags = ["--delete"] if delete else []
    completed = subprocess.run(  # noqa: S603
        ["rsync", *flags, *args, src, dest],
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        check=True,
    )
    return completed.stdout

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

#: Long options refused in ``args``, from the ``--help`` of openrsync (the
#: macOS rsync) and GNU rsync 3. Each one deletes files, writes outside the
#: named destination, reads from somewhere other than the named source, or
#: runs another program:
#:
#: - delete: ``--del`` / ``--delete*``, ``--remove-source-files`` and its old
#:   name ``--remove-sent-files``, and ``--force``, which deletes non-empty
#:   destination directories that a file replaces;
#: - write elsewhere: ``--backup-dir``, ``--log-file``, ``--write-batch``,
#:   ``--only-write-batch``, ``--partial-dir``, ``--temp-dir``, and
#:   ``--keep-dirlinks``, which follows symlinked directories in the
#:   destination;
#: - read elsewhere: ``--compare-dest``, ``--copy-dest``, ``--link-dest``,
#:   ``--files-from``, ``--read-batch``, ``--early-input``, and
#:   ``--password-file``, which reads a local file and sends it to the daemon;
#: - run programs or steer the remote side: ``--rsh``, ``--rsync-path``,
#:   ``--remote-option``, ``--daemon``, ``--server``, ``--sender``.
#:
#: openrsync accepts unambiguous abbreviations (``--remove-sou``), so an
#: option is refused when its name starts with, or abbreviates, one of these.
REFUSED_LONG_OPTIONS = [
    "--delete",
    "--remove-source-files",
    "--remove-sent-files",
    "--force",
    "--backup-dir",
    "--log-file",
    "--write-batch",
    "--only-write-batch",
    "--partial-dir",
    "--temp-dir",
    "--keep-dirlinks",
    "--compare-dest",
    "--copy-dest",
    "--link-dest",
    "--files-from",
    "--read-batch",
    "--early-input",
    "--password-file",
    "--rsh",
    "--rsync-path",
    "--remote-option",
    "--daemon",
    "--server",
    "--sender",
]

#: Complete option names that abbreviate a refused one but are safe:
#: ``--backup`` and ``--partial`` keep their files inside the destination.
SAFE_LONG_OPTIONS = ["--backup", "--partial"]

#: Short options refused anywhere in a short-option group such as ``-avz``:
#: ``-e`` (remote shell program), ``-T`` (temporary directory), ``-M``
#: (option passed to the remote rsync) and ``-K`` (``--keep-dirlinks``).
REFUSED_SHORT_OPTIONS = "eTMK"


def has_no_refused_option_names(args: list[str]) -> bool:
    """Whether ``args`` has none of the refused options spelled exactly.

    The precondition form of the body's check: exact names only, because the
    prover cannot match prefixes inside a list. Abbreviations, ``=value``
    forms and short-option groups are refused by the body when the program
    runs.
    """
    return (
        "--del" not in args
        and "--delete" not in args
        and "--remove-source-files" not in args
        and "--remove-sent-files" not in args
        and "--force" not in args
        and "--backup-dir" not in args
        and "--log-file" not in args
        and "--write-batch" not in args
        and "--only-write-batch" not in args
        and "--partial-dir" not in args
        and "--temp-dir" not in args
        and "--keep-dirlinks" not in args
        and "--compare-dest" not in args
        and "--copy-dest" not in args
        and "--link-dest" not in args
        and "--files-from" not in args
        and "--read-batch" not in args
        and "--early-input" not in args
        and "--password-file" not in args
        and "--rsh" not in args
        and "--rsync-path" not in args
        and "--remote-option" not in args
        and "--daemon" not in args
        and "--server" not in args
        and "--sender" not in args
        and "-e" not in args
        and "-T" not in args
        and "-M" not in args
        and "-K" not in args
    )


def _refused(arg: str) -> bool:
    """Whether rsync would read ``arg`` as a refused option.

    >>> [_refused(a) for a in ["-avz", "--partial", "--remove-sou", "-avze"]]
    [False, False, True, True]
    """
    if arg.startswith("--"):
        name = arg.split("=", 1)[0]
        if name in SAFE_LONG_OPTIONS:
            return False
        return any(
            name.startswith(option) or option.startswith(name)
            for option in REFUSED_LONG_OPTIONS
        )
    if arg.startswith("-"):
        return any(letter in REFUSED_SHORT_OPTIONS for letter in arg[1:])
    return False


@clauz3.requires(
    lambda src, dest, delete, args: (
        not src.startswith("-") and not dest.startswith("-")
    ),
    message="src and dest must not start with '-' (they would be options)",
)
@clauz3.requires(
    lambda src, dest, delete, args: has_no_refused_option_names(args),
    message="args must not delete, write or read elsewhere, or run programs",
)
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

    ``src`` and ``dest`` may not start with ``-``: rsync reads options even
    after the paths, so ``dest="--delete"`` would turn on deletion and make
    the last path in ``args`` the destination.

    ``args`` may not contain an option that deletes, writes outside ``dest``,
    reads other than from the named sources, or runs another program (see
    ``REFUSED_LONG_OPTIONS`` and ``REFUSED_SHORT_OPTIONS``); one raises
    ``ValueError`` before rsync runs. Exact spellings are also preconditions
    the prover checks; abbreviations, ``=value`` forms and short-option
    groups are refused only when the program runs, possibly after earlier
    calls. Preconditions are checked again in the body, because Deal's
    runtime checks are optional. Ordinary short-option groups such as
    ``-avz`` are fine. A short-option group whose attached value contains
    ``e``, ``T`` or ``M`` (``-f"merge rules"``) is refused too; pass such values
    with the long option (``--filter``). A plain path in ``args`` is an extra
    source, which ``only_src_under`` does not check.
    """
    if not (len(src) > 0 and len(dest) > 0):
        raise ValueError("src and dest must be non-empty")
    if not (has_no_parent_segments(src) and has_no_parent_segments(dest)):
        raise ValueError("src and dest must not contain parent-directory segments")
    if src.startswith("-") or dest.startswith("-"):
        raise ValueError("src and dest must not start with '-'")
    for arg in args:
        if _refused(arg):
            raise ValueError(f"rsync option {arg!r} is refused")
    flags = ["--delete"] if delete else []
    completed = subprocess.run(  # noqa: S603
        ["rsync", *flags, *args, src, dest],
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        check=True,
    )
    return completed.stdout

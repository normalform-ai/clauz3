"""Filesystem policies, stated over the events the tools perform.

Each contract below is a query over an event class from ``events.py``, so it
governs every tool that performs that event: ``never_read_under`` covers
``read_file``, ``read_lines``, ``read_line``, ``grep`` and the source of
``copy`` and ``move``; ``only_write_under`` covers every modification,
including deletes, creates and chmods.

Every contract covers this library's tools only. Effects of other libraries
that touch files (git, rsync, db) are not events here, and the approval text
says so by naming "filesystem tools".

Prefix checks are literal string prefixes, not resolved directory containment:
``only_read_under("test.md")`` also admits ``test.md.bak``. The ``never_*``
contracts also refuse an event whose path is a prefix of the forbidden one,
since that path may be a directory containing it. The effects refuse ``..``,
``/./`` and ``//`` so a path cannot be respelled around a prefix, but the check
stays lexical. A relative prefix is compared in both its bare and ``./``
spellings, and ``.`` counts as an ancestor of every relative path, but a
relative prefix says nothing about an absolute spelling of the same file.
Symbolic links, hard links and case-insensitive filesystems are not modelled.

``Delete`` means ``remove`` or a ``move``'s source. Overwriting a file with
``write_file``, ``copy`` or ``move`` is a ``Write``, not a ``Delete``: protect
contents with ``never_write_under``. Parent directories that ``write_file``,
``touch`` and ``copy`` create on the way are not ``Create`` events; they lie on
the path of the ``Write`` that created them.

A few contracts are about one tool's own arguments (``only_pattern``,
``only_modes``) and are stated over that tool or event alone.
"""

from tools.filesystem.trusted.effects import grep
from tools.filesystem.trusted.events import (
    Chmod,
    Create,
    Delete,
    FsEvent,
    List,
    Observe,
    Read,
    Write,
    WriteText,
)

from clauz3._path_safety import has_no_parent_segments, has_plain_segments
from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Grep = effect(grep)


def _checked(prefix: str) -> str:
    """Refuse a prefix no effect path can be written as.

    Every effect refuses ``..``, ``/./``, ``//`` and backslashes, so a policy
    over ``data//secret`` would match nothing while ``data/secret`` stayed
    unconstrained. Raising here makes the proof a refusal, not a vacuous pass.

    >>> _checked("data/./secret")
    Traceback (most recent call last):
    ...
    ValueError: path prefix 'data/./secret' is not written the way effect paths are
    """
    if not (has_no_parent_segments(prefix) and has_plain_segments(prefix)):
        raise ValueError(
            f"path prefix {prefix!r} is not written the way effect paths are"
        )
    return prefix


def _under(event: type[FsEvent], root: str) -> ContractSpec:
    _checked(root)
    return event.all(lambda e: e.path.startswith(root))


def _spellings(prefix: str) -> tuple[str, str]:
    """A prefix written bare and with ``./``; the effects admit both spellings.

    ``.`` and ``./`` are the working directory, the bare prefix ``""``.

    >>> _spellings("./data/.env"), _spellings("data"), _spellings("/etc")
    (('data/.env', './data/.env'), ('data', './data'), ('/etc', '/etc'))
    >>> _spellings("."), _spellings("./")
    (('', './'), ('', './'))
    """
    if prefix.startswith("/"):
        return prefix, prefix
    bare = "" if prefix == "." else prefix.removeprefix("./")
    return bare, "./" + bare


def _never_under(event: type[FsEvent], prefix: str) -> ContractSpec:
    # An event is refused if its path is under either spelling of the prefix,
    # or is a prefix of either one: an ancestor directory, including ``.``.
    bare, dotted = _spellings(_checked(prefix))
    return event.all(
        lambda e: (
            not e.path.startswith(bare)
            and not e.path.startswith(dotted)
            and not bare.startswith(e.path)
            and not dotted.startswith(e.path)
        )
    )


@contract(description="No restrictions are imposed on filesystem effects.")
def no_guarantees() -> ContractSpec:
    """Make no guarantees about filesystem effects."""
    return core_no_guarantees()


# -- reading -----------------------------------------------------------------


@contract(description="No filesystem tool reads a file or lists a directory.")
def no_reads() -> ContractSpec:
    """Guarantee the program neither reads files nor lists directories."""
    return Observe.empty()


@contract(description="Every file a filesystem tool reads or lists starts with {root}.")
def only_read_under(root: str) -> ContractSpec:
    """Guarantee every read, search, listing, and copy or move source is under
    ``root``."""
    return _under(Observe, root)


@contract(
    description=(
        "No filesystem tool reads, copies or moves a path starting with "
        "{prefix}, or a directory containing it (paths compared as written)."
    )
)
def never_read_under(prefix: str) -> ContractSpec:
    """Guarantee no file contents under ``prefix`` reach the program or a copy.

    Listing a directory that contains ``prefix`` is allowed: it reveals the
    name, not the contents. Use ``only_read_under`` to confine listings too.
    """
    return _never_under(Read, prefix)


@contract(description="Filesystem tools read files at most {count} times.")
def reads_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` reads (a copy or move reads its source)."""
    return Read.count() <= count


@contract(description="Filesystem tools list directories at most {count} times.")
def lists_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` ``find`` walks occur."""
    return List.count() <= count


@contract(description="Every grep search uses exactly the pattern {pattern}.")
def only_pattern(pattern: str) -> ContractSpec:
    """Guarantee every ``grep`` search uses exactly this pattern."""
    return Grep.all(lambda e: e.pattern == pattern)


# -- writing -----------------------------------------------------------------


@contract(
    description=(
        "No filesystem tool modifies anything. Effects of other libraries, such"
        " as git or rsync, are not covered."
    )
)
def read_only() -> ContractSpec:
    """Guarantee no write, append, create, delete, move, copy or chmod occurs."""
    return Write.empty()


@contract(
    description=(
        "Every modification by a filesystem tool is to a path starting with {root}."
    )
)
def only_write_under(root: str) -> ContractSpec:
    """Guarantee every modification, of any kind, has a path under ``root``.

    A move deletes its source, so moving a file out of ``root`` is refused too.
    """
    return _under(Write, root)


@contract(
    description=(
        "No filesystem tool modifies a path starting with {prefix}, or a "
        "directory containing it (paths compared as written)."
    )
)
def never_write_under(prefix: str) -> ContractSpec:
    """Guarantee no modification, of any kind, touches ``prefix``."""
    return _never_under(Write, prefix)


@contract(description="Filesystem tools make at most {count} modifications.")
def writes_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` modifications (a move counts twice)."""
    return Write.count() <= count


@contract(
    description="No text a filesystem tool writes to a file contains {substring}."
)
def must_not_write(substring: str) -> ContractSpec:
    """Guarantee no written or appended text contains ``substring``.

    A secrets / banned-content guard before content reaches disk. Copies and
    moves write existing contents, not text, so they are not covered.
    """
    return WriteText.all(lambda e: substring not in e.text)


@contract(
    description=(
        "Text a filesystem tool writes to a file is at most {max_chars} characters."
    )
)
def write_length_at_most(max_chars: int) -> ContractSpec:
    """Guarantee every written or appended text is at most ``max_chars`` long."""
    return WriteText.all(lambda e: len(e.text) <= max_chars)


# -- deleting ----------------------------------------------------------------


@contract(description="No filesystem tool deletes or moves anything away.")
def no_deletes() -> ContractSpec:
    """Guarantee nothing is removed, including by moving it.

    Overwriting a file is a write, not a delete; see ``never_write_under``.
    """
    return Delete.empty()


@contract(
    description="Everything a filesystem tool deletes or moves away starts with {root}."
)
def only_delete_under(root: str) -> ContractSpec:
    """Guarantee every removed (or moved) path is under ``root``."""
    return _under(Delete, root)


@contract(
    description=(
        "No filesystem tool deletes or moves a path starting with {prefix}, or "
        "a directory containing it (paths compared as written)."
    )
)
def never_delete_under(prefix: str) -> ContractSpec:
    """Guarantee nothing under ``prefix`` is removed or moved.

    The blast-radius guard for handing an agent ``rm``: e.g.
    ``never_delete_under("/home/user/.ssh")``.
    """
    return _never_under(Delete, prefix)


@contract(description="Filesystem tools delete or move away at most {count} paths.")
def deletes_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` removals (a move counts as one)."""
    return Delete.count() <= count


# -- creating ----------------------------------------------------------------


@contract(description="No filesystem tool creates a directory or empty file.")
def no_creates() -> ContractSpec:
    """Guarantee no ``make_dir`` or ``touch`` occurs."""
    return Create.empty()


@contract(
    description="Filesystem tools create at most {count} directories or empty files."
)
def creates_at_most(count: int) -> ContractSpec:
    """Guarantee at most ``count`` directories or empty files are created."""
    return Create.count() <= count


# -- permissions -------------------------------------------------------------


@contract(description="No filesystem tool changes permission bits.")
def no_chmods() -> ContractSpec:
    """Guarantee the program changes no permission bits."""
    return Chmod.empty()


@contract(
    description="Every filesystem chmod sets a mode in {allowlist}, written in decimal."
)
def only_modes(allowlist: list[int]) -> ContractSpec:
    """Guarantee every chmod sets a mode in ``allowlist``.

    Bitwise masking is not expressible in a contract lambda, so permission
    safety is stated as an explicit allowlist of whole modes, e.g.
    ``only_modes([0o644, 0o755, 0o600, 0o700])`` — which by construction admits
    no world-writable, setuid, or setgid bits.
    """
    return Chmod.all(lambda e: e.mode in allowlist)

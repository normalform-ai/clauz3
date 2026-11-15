"""Trusted filesystem effects: read, search, list, write, edit and manage files.

These are real (non-mock) side-effecting functions. The prover never executes
their bodies: it records each call as an effect fact, proves the declared
preconditions, and reads the call as the events in ``@performs`` (see
``events.py``). The bodies run only under ``clauz3 run``, after a program has
been proved and approved.

The bodies use ``pathlib``/``shutil``/``os`` directly, not a shell, so no
argument is ever interpreted by a shell. ``find`` and ``search_files`` are the
exceptions: they run the ``find`` and ``rg`` binaries with fixed argument
vectors.

The ``@deal.has(...)`` markers are deal's own: they buy its runtime guard and
the ``clauz3 lint`` check on these bodies, and play no part in what a contract
can say.
"""

import base64
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from pathlib import Path

import deal
from tools.filesystem.trusted.events import (
    Chmod,
    Create,
    Delete,
    DirectoryCreate,
    List,
    Read,
    RecursiveRead,
    RecursiveWrite,
    WriteText,
)
from tools.filesystem.trusted.schemas import Match

import clauz3
from clauz3._path_safety import is_plain_path
from clauz3.events import performs

DEFAULT_TIMEOUT_SECONDS = 120


# -- reading ----------------------------------------------------------------


@clauz3.requires(
    lambda path: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("read")
@clauz3.trusted
@performs(lambda path: Read(path))
def read_file(path: str) -> str:
    """Read and return the UTF-8 text contents of ``path``."""
    return Path(path).read_text(encoding="utf-8")


@clauz3.requires(
    lambda path: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("read")
@clauz3.trusted
@performs(lambda path: Read(path))
def read_lines(path: str) -> list[str]:
    """Read UTF-8 logical lines without terminators, using str.splitlines.

    Empty files return []; blank lines are retained; a final unterminated
    line counts. This is not wc -l, which counts LF bytes. Decode and I/O
    errors propagate, as with read_file.
    """
    return Path(path).read_text(encoding="utf-8").splitlines()


@clauz3.requires(
    lambda path, line_number: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(
    lambda path, line_number: len(path) >= 1 and line_number >= 1,
    message="path must be non-empty and line_number must be positive",
)
@deal.has("read")
@clauz3.trusted
@performs(lambda path: Read(path))
def read_line(path: str, line_number: int) -> str:
    """Read a one-based line, without its terminator; return empty at EOF.

    Decode UTF-8 with universal newline handling. Preserve all other whitespace.
    """
    with Path(path).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            if number == line_number:
                return line.removesuffix("\n")
    return ""


@clauz3.requires(
    lambda pattern, path: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(
    lambda pattern, path: len(pattern) >= 1 and len(path) >= 1,
    message="pattern and path must be non-empty",
)
@deal.has("read")
@clauz3.trusted
@performs(lambda path: Read(path))
def grep(pattern: str, path: str) -> list[str]:
    """Return the lines in ``path`` that contain the substring ``pattern``."""
    text = Path(path).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if pattern in line]


def _filters(args: list[str]) -> list[str]:
    """Parse conjunctions of filters, optionally negating one ordinary filter."""
    result: list[str] = []
    depths: list[str] = []
    index = 0
    while index < len(args):
        negate = args[index] == "-not"
        if negate:
            index += 1
        if index + 1 >= len(args):
            raise ValueError(
                "find expects filter/value pairs, optionally prefixed by -not"
            )
        option, value = args[index : index + 2]
        if "\0" in value:
            raise ValueError("find filter values must not contain NUL")
        if option in {"-name", "-path"}:
            pass
        elif option == "-type":
            if value not in {"f", "d", "l"}:
                raise ValueError("find -type supports only f, d, or l")
        elif option in {"-mindepth", "-maxdepth"}:
            if negate:
                raise ValueError("find depth limits cannot be negated")
            if not value or not value.isascii() or not value.isdecimal():
                raise ValueError("find depth must be a non-negative decimal integer")
        else:
            raise ValueError(f"unsupported find filter: {option}")
        if negate:
            result.append("-not")
        if option in {"-mindepth", "-maxdepth"}:
            depths.extend((option, value))
        else:
            result.extend((option, value))
        index += 2
    # GNU find expects global depth options before ordinary expression tests.
    return depths + result


@clauz3.requires(
    lambda path, args: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(lambda path, args: len(path) > 0, message="path must be non-empty")
@deal.has("read", "io", "syscall")
@clauz3.trusted
@performs(lambda path: List(path))
def find(path: str, args: list[str]) -> list[str]:
    """List paths using -name, -path, -type f/d/l, -mindepth, and -maxdepth.

    Filters are combined with AND. -not may negate a name/path/type filter.
    Invalid arguments raise ValueError before starting a subprocess. Traversal
    uses -P for encountered symlinks; root-path resolution can still follow
    symlinks, including roots ending in "/" or "/.". Output is NUL-delimited.
    Contracts constrain the lexical starting path, not resolved confinement.
    """
    filters = _filters(args)
    # Do not rely on Deal's optional runtime checks at this external boundary.
    if not path or "\0" in path or not is_plain_path(path):
        raise ValueError(
            "find requires a non-empty path without .., . or empty segments, "
            "backslashes or NUL"
        )
    # Prefix relative roots so find cannot parse an option or expression
    # such as '-L', '!', or '(' in place of the one selected starting path.
    root = (
        path
        if path == "." or os.path.isabs(path) or path.startswith("./")
        else "./" + path
    )
    completed = subprocess.run(  # noqa: S603
        ["find", "-P", root, *filters, "-print0"],
        capture_output=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        check=True,
    )
    return [os.fsdecode(item) for item in completed.stdout.split(b"\0") if item]


def _rg_text(value: dict[str, str]) -> str:
    """Decode a ripgrep JSON text field, which is base64 bytes when not UTF-8."""
    if "text" in value:
        return value["text"]
    return os.fsdecode(base64.b64decode(value["bytes"]))


@clauz3.requires(
    lambda pattern, path, glob, max_results: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(
    lambda pattern, path, glob, max_results: len(pattern) > 0 and len(path) > 0,
    message="pattern and path must be non-empty",
)
@clauz3.requires(
    lambda pattern, path, glob, max_results: max_results >= 1 and max_results <= 1000,
    message="max_results must be between 1 and 1000",
)
@clauz3.ensures(
    lambda pattern, path, glob, max_results, result: len(result) <= max_results
)
@deal.has("read", "io", "syscall", "import")
@clauz3.trusted
@performs(lambda path: RecursiveRead(path))
def search_files(pattern: str, path: str, glob: str, max_results: int) -> list[Match]:
    """Return up to ``max_results`` lines under ``path`` matching ``pattern``.

    ``pattern`` is a ripgrep (Rust) regular expression; the search is recursive.
    ``glob`` limits file names, for example ``*.py``; an empty string searches
    all files. ``max_results`` must be between 1 and 1000. Each result is a
    ``Match`` with the file path, one-based line number and line text.

    ripgrep's defaults apply: ``.gitignore``d (inside a git repository) and
    binary files are skipped, hidden files and directories are skipped
    unless ``glob`` matches them (``glob="*"`` matches every name, so it
    searches hidden files too, and ``".*"`` searches only hidden files),
    and a symlink encountered under ``path`` is not followed -- but a
    symlink given as ``path`` itself is followed. The user's own ripgrep
    configuration (``RIPGREP_CONFIG_PATH`` and the rest) is ignored, so
    these defaults cannot be changed from outside the call. Ignore files
    are honoured only at or below ``path``: a ``.gitignore`` above ``path``
    (including the global and repository excludes) is not read, so a
    repository-root ``.gitignore`` applies when searching ``.`` but not
    when searching a subdirectory of it; use ``glob`` to narrow a search
    instead. No matches returns ``[]``. ripgrep searches files in parallel,
    so which matches are returned when
    ``max_results`` is reached is unspecified. Any ripgrep error -- an
    invalid pattern, a missing ``path``, or a single unreadable file under
    it -- raises ``subprocess.CalledProcessError`` with no partial results,
    unless ``max_results`` is reached first, in which case the matches found
    so far are returned and no error is raised. A search that takes longer
    than two minutes (``DEFAULT_TIMEOUT_SECONDS``) is killed and raises
    ``subprocess.TimeoutExpired``, also with no partial results. Contracts
    see a recursive read of ``path``.
    """
    # No separate body check is needed: ``-e``, ``--`` and the ``./`` prefix
    # stop option injection, and the plain-path precondition is proved first.
    # The prefix is stripped again from reported paths.
    prefixed = not (path == "." or os.path.isabs(path) or path.startswith("./"))
    root = "./" + path if prefixed else path
    command = [
        "rg",
        "--no-config",
        "--no-ignore-parent",
        "--no-ignore-global",
        "--no-ignore-exclude",
        "--json",
        "-e",
        pattern,
    ]
    if glob:
        command += ["--glob", glob]
    command += ["--", root]
    matches: list[Match] = []
    with subprocess.Popen(  # noqa: S603
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as process:
        assert process.stdout is not None
        assert process.stderr is not None
        # stderr is drained by its own thread, so a large stderr never fills
        # its pipe and blocks rg while stdout is being read.
        stderr = process.stderr
        errors: list[bytes] = []
        drain = threading.Thread(target=lambda: errors.append(stderr.read()))
        drain.start()
        # process.wait(timeout=...) below only runs once stdout ends, so a
        # slow or hung rg would otherwise block forever. This timer bounds
        # the whole call, including the time spent streaming matches.
        timed_out = threading.Event()

        def _kill_on_timeout() -> None:
            timed_out.set()
            process.kill()

        timer = threading.Timer(DEFAULT_TIMEOUT_SECONDS, _kill_on_timeout)
        timer.start()
        try:
            for line in process.stdout:
                # A line read after the timer's kill may be truncated.
                if timed_out.is_set():
                    break
                # rg's JSON lines are UTF-8; json.loads decodes the bytes.
                event = json.loads(line)
                if event["type"] != "match":
                    continue
                data = event["data"]
                found = _rg_text(data["path"])
                matches.append(
                    Match(
                        path=found.removeprefix("./") if prefixed else found,
                        line_number=data["line_number"],
                        text=_rg_text(data["lines"])
                        .removesuffix("\n")
                        .removesuffix("\r"),
                    )
                )
                if len(matches) == max_results:
                    process.kill()
                    return matches
            if timed_out.is_set():
                raise subprocess.TimeoutExpired(command, DEFAULT_TIMEOUT_SECONDS)
            returncode = process.wait(timeout=DEFAULT_TIMEOUT_SECONDS)
            # The timer may fire while waiting for the exit status: rg then
            # dies of the kill, which is a timeout, not a ripgrep error.
            if timed_out.is_set():
                raise subprocess.TimeoutExpired(command, DEFAULT_TIMEOUT_SECONDS)
            if returncode not in (0, 1):
                drain.join()
                raise subprocess.CalledProcessError(
                    returncode,
                    command,
                    stderr=errors[0].decode("utf-8", errors="replace"),
                )
        finally:
            timer.cancel()
    return matches


# -- writing ----------------------------------------------------------------


@clauz3.requires(
    lambda path, content: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(lambda path, content: len(path) > 0, message="path must be non-empty")
@deal.has("write")
@clauz3.trusted
@performs(lambda path, content: WriteText(path, content))
def write_file(path: str, content: str) -> None:
    """Write ``content`` to ``path`` as UTF-8 text, creating parent dirs."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


@clauz3.requires(
    lambda path, text: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(lambda path, text: len(path) > 0, message="path must be non-empty")
@deal.has("write")
@clauz3.trusted
@performs(lambda path, text: WriteText(path, text))
def append_file(path: str, text: str) -> None:
    """Append ``text`` to the UTF-8 contents of ``path``.

    Creates parent directories and the file as needed.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(text)


@clauz3.requires(
    lambda path, old, new, replace_all=False: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(
    lambda path, old, new, replace_all=False: len(path) > 0 and len(old) > 0,
    message="path and old must be non-empty",
)
@clauz3.requires(
    lambda path, old, new, replace_all=False: old != new,
    message="old and new must differ",
)
@deal.has("read", "write")
@clauz3.trusted
@performs(lambda path, new: [Read(path), WriteText(path, new)])
def edit_file(path: str, old: str, new: str, replace_all: bool = False) -> None:
    """Replace the exact text ``old`` with ``new`` in the existing file ``path``.

    Occurrences are counted at every position ``old`` starts, including
    overlapping ones (``"aaa"`` contains two occurrences of ``"aa"``). ``old``
    must occur exactly once unless ``replace_all`` is true, in which case
    every non-overlapping occurrence, taken left to right, is replaced, and at
    least one must exist. Otherwise ``ValueError`` reports how many were
    found and the file is unchanged. The file must exist and be writable;
    this never creates one, and raises ``PermissionError`` rather than
    rewrite a read-only file. Line endings and permission bits are preserved.

    The new contents are written to a temporary file in the same directory
    and then renamed over ``path``, so this needs write permission on the
    directory as well as the file, and a process killed between the write
    and the rename can leave a stray ``.NAME.*.tmp`` sibling behind; the
    rename itself is atomic, so ``path`` is never left half-written.

    Contracts see a read of ``path`` and a text write of ``new`` to ``path``.
    ``must_not_write`` and ``write_length_at_most`` therefore check only the
    inserted text, not the resulting file. The temporary sibling is not
    seen: like the parent directories ``write_file`` creates, it is not
    listed by exact-file contracts such as ``only_writes_files``, though it
    sits in the same directory as ``path``. A symlink at ``path`` is replaced
    by a regular file holding the new contents (the link's target is not
    changed), as are hard links; ownership is not preserved.
    """
    target = Path(path)
    with target.open(encoding="utf-8", newline="") as stream:
        text = stream.read()
    if not os.access(target, os.W_OK):
        raise PermissionError(f"edit_file cannot write {path}: file is not writable")
    found = sum(1 for _ in re.finditer(f"(?={re.escape(old)})", text))
    if found == 0 or (found > 1 and not replace_all):
        raise ValueError(
            f"edit_file needs exactly one occurrence of old in {path}, "
            f"found {found}; pass replace_all=True to replace every one"
            if found
            else f"edit_file found 0 occurrences of old in {path}"
        )
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    mode = target.stat().st_mode
    descriptor, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(updated)
        os.chmod(temporary, stat.S_IMODE(mode))
        os.replace(temporary, target)
    except BaseException:
        # Remove the partial temporary file, then let the error propagate.
        Path(temporary).unlink(missing_ok=True)
        raise


# -- managing files and directories ------------------------------------------


@clauz3.requires(
    lambda path: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("write", "io")
@clauz3.trusted
@performs(lambda path: DirectoryCreate(path))
def make_dir(path: str) -> None:
    """Create directory ``path`` (with parents)."""
    Path(path).mkdir(parents=True, exist_ok=True)


@clauz3.requires(
    lambda path: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("write", "io")
@clauz3.trusted
@performs(lambda path: Create(path))
def touch(path: str) -> None:
    """Create empty file ``path`` (or bump its mtime)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()


@clauz3.requires(
    lambda path: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("write", "io")
@clauz3.trusted
@performs(lambda path: Delete(path))
def remove(path: str) -> None:
    """Delete ``path`` — a file, or a directory and everything under it."""
    target = Path(path)
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)


@clauz3.requires(
    lambda src, dest: is_plain_path(src) and is_plain_path(dest),
    message="src and dest must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(
    lambda src, dest: len(src) > 0 and len(dest) > 0,
    message="src and dest must be non-empty",
)
@deal.has("read", "write", "io")
@clauz3.trusted
@performs(lambda src, dest: [RecursiveRead(src), Delete(src), RecursiveWrite(dest)])
def move(src: str, dest: str) -> None:
    """Move ``src`` to ``dest``.

    A move is read as reading and deleting its source and writing its
    destination, so a file cannot be moved out from under a read policy and
    read under its new name.
    """
    shutil.move(src, dest)


@clauz3.requires(
    lambda src, dest: is_plain_path(src) and is_plain_path(dest),
    message="src and dest must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(
    lambda src, dest: len(src) > 0 and len(dest) > 0,
    message="src and dest must be non-empty",
)
@deal.has("read", "write", "io")
@clauz3.trusted
@performs(lambda src, dest: [RecursiveRead(src), RecursiveWrite(dest)])
def copy(src: str, dest: str) -> None:
    """Copy ``src`` to ``dest`` (file or directory tree)."""
    if Path(src).is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


@clauz3.requires(
    lambda path, mode: is_plain_path(path),
    message="path must have no .., . or empty segments, or backslashes",
)
@clauz3.requires(lambda path, mode: len(path) > 0, message="path must be non-empty")
@deal.has("io")
@clauz3.trusted
@performs(lambda path, mode: Chmod(path, mode))
def chmod(path: str, mode: int) -> None:
    """Set the permission bits of ``path`` to ``mode``."""
    os.chmod(path, mode)

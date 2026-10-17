"""Trusted filesystem effects: read, search, list, write, edit and manage files.

These are real (non-mock) side-effecting functions. The prover never executes
their bodies: it records each call as an effect fact, proves the declared
preconditions, and reads the call as the events in ``@performs`` (see
``events.py``). The bodies run only under ``clauz3 run``, after a program has
been proved and approved.

The bodies use ``pathlib``/``shutil``/``os`` directly, not a shell, so no
argument is ever interpreted by a shell. ``find`` is the exception: it runs the
``find`` binary with an allowlisted argument grammar.

The ``@deal.has(...)`` markers are deal's own: they buy its runtime guard and
the ``clauz3 lint`` check on these bodies, and play no part in what a contract
can say.
"""

import os
import shutil
import subprocess
from pathlib import Path

import deal
from tools.filesystem.trusted.events import (
    Chmod,
    Create,
    Delete,
    List,
    Read,
    Write,
    WriteText,
)

import clauz3
from clauz3._path_safety import has_no_dot_segments, has_no_parent_segments
from clauz3.events import performs

DEFAULT_TIMEOUT_SECONDS = 120


# -- reading ----------------------------------------------------------------


@clauz3.requires(
    lambda path: has_no_parent_segments(path) and has_no_dot_segments(path),
    message="path must not contain parent, current-directory or empty segments",
)
@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("read")
@clauz3.trusted
@performs(lambda path: Read(path))
def read_file(path: str) -> str:
    """Read and return the UTF-8 text contents of ``path``."""
    return Path(path).read_text(encoding="utf-8")


@clauz3.requires(
    lambda path: has_no_parent_segments(path) and has_no_dot_segments(path),
    message="path must not contain parent, current-directory or empty segments",
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
    lambda path, line_number: (
        has_no_parent_segments(path) and has_no_dot_segments(path)
    ),
    message="path must not contain parent, current-directory or empty segments",
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
    lambda pattern, path: has_no_parent_segments(path) and has_no_dot_segments(path),
    message="path must not contain parent, current-directory or empty segments",
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
    lambda path, args: has_no_parent_segments(path) and has_no_dot_segments(path),
    message="path must not contain parent, current-directory or empty segments",
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
    if (
        not path
        or "\0" in path
        or not has_no_parent_segments(path)
        or not has_no_dot_segments(path)
    ):
        raise ValueError(
            "find requires a non-empty path without parent, current-directory "
            "or empty segments, or NUL"
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


# -- writing ----------------------------------------------------------------


@clauz3.requires(
    lambda path, content: has_no_parent_segments(path) and has_no_dot_segments(path),
    message="path must not contain parent, current-directory or empty segments",
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
    lambda path, text: has_no_parent_segments(path) and has_no_dot_segments(path),
    message="path must not contain parent, current-directory or empty segments",
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


# -- managing files and directories ------------------------------------------


@clauz3.requires(
    lambda path: has_no_parent_segments(path) and has_no_dot_segments(path),
    message="path must not contain parent, current-directory or empty segments",
)
@clauz3.requires(lambda path: len(path) > 0, message="path must be non-empty")
@deal.has("write", "io")
@clauz3.trusted
@performs(lambda path: Create(path))
def make_dir(path: str) -> None:
    """Create directory ``path`` (with parents)."""
    Path(path).mkdir(parents=True, exist_ok=True)


@clauz3.requires(
    lambda path: has_no_parent_segments(path) and has_no_dot_segments(path),
    message="path must not contain parent, current-directory or empty segments",
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
    lambda path: has_no_parent_segments(path) and has_no_dot_segments(path),
    message="path must not contain parent, current-directory or empty segments",
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
    lambda src, dest: (
        has_no_parent_segments(src)
        and has_no_dot_segments(src)
        and has_no_parent_segments(dest)
        and has_no_dot_segments(dest)
    ),
    message="src and dest must not contain parent, current-directory or empty segments",
)
@clauz3.requires(
    lambda src, dest: len(src) > 0 and len(dest) > 0,
    message="src and dest must be non-empty",
)
@deal.has("read", "write", "io")
@clauz3.trusted
@performs(lambda src, dest: [Read(src), Delete(src), Write(dest)])
def move(src: str, dest: str) -> None:
    """Move ``src`` to ``dest``.

    A move is read as reading and deleting its source and writing its
    destination, so a file cannot be moved out from under a read policy and
    read under its new name.
    """
    shutil.move(src, dest)


@clauz3.requires(
    lambda src, dest: (
        has_no_parent_segments(src)
        and has_no_dot_segments(src)
        and has_no_parent_segments(dest)
        and has_no_dot_segments(dest)
    ),
    message="src and dest must not contain parent, current-directory or empty segments",
)
@clauz3.requires(
    lambda src, dest: len(src) > 0 and len(dest) > 0,
    message="src and dest must be non-empty",
)
@deal.has("read", "write", "io")
@clauz3.trusted
@performs(lambda src, dest: [Read(src), Write(dest)])
def copy(src: str, dest: str) -> None:
    """Copy ``src`` to ``dest`` (file or directory tree)."""
    if Path(src).is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


@clauz3.requires(
    lambda path, mode: has_no_parent_segments(path) and has_no_dot_segments(path),
    message="path must not contain parent, current-directory or empty segments",
)
@clauz3.requires(lambda path, mode: len(path) > 0, message="path must be non-empty")
@deal.has("io")
@clauz3.trusted
@performs(lambda path, mode: Chmod(path, mode))
def chmod(path: str, mode: int) -> None:
    """Set the permission bits of ``path`` to ``mode``."""
    os.chmod(path, mode)

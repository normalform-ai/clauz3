"""Read-only find traversal with an explicit allowlist of filter arguments.

The trusted boundary parses all arguments before invoking the binary. Only
name/path/type filters and depth limits are available; callers cannot supply
find actions or change traversal/output behavior.
"""

import os
import subprocess

import deal

import clauz3
from clauz3._path_safety import has_no_parent_segments

DEFAULT_TIMEOUT_SECONDS = 120


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
    lambda path, args: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(lambda path, args: len(path) > 0, message="path must be non-empty")
@deal.has("read", "io", "syscall")
@clauz3.trusted
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
    if not path or "\0" in path or not has_no_parent_segments(path):
        raise ValueError(
            "find requires a non-empty path without parent segments or NUL"
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

"""Regression coverage for lexical path traversal in trusted effects."""

from pathlib import Path, PureWindowsPath

import pytest

from clauz3._path_safety import (
    has_no_parent_segments,
    is_single_path_component,
    url_has_no_parent_segments,
)
from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProofResult, prove_text

ROOT = Path(__file__).resolve().parents[1]
FILESYSTEM_ROOT = ROOT / "src/clauz3/stdlib/filesystem"
FILESYSTEM_TRUSTED = FILESYSTEM_ROOT / "tools/filesystem/trusted"

HEADER = """\
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import write_file
"""


def _prove(body: str) -> ProofResult:
    results = prove_text(
        HEADER + body,
        import_paths=[FILESYSTEM_ROOT],
        trusted_roots=[FILESYSTEM_TRUSTED],
    )
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize(
    "path",
    [
        "..",
        "../escape",
        "..\\escape",
        "/sandbox/../escape",
        "/sandbox/..\\escape",
        "C:\\sandbox\\../escape",
        "C:\\sandbox\\..\\escape",
        "/sandbox/..",
        "C:\\sandbox\\..",
    ],
)
def test_parent_segments_are_lexically_rejected(path: str) -> None:
    assert not has_no_parent_segments(path)


@pytest.mark.parametrize(
    "path",
    [
        ".",
        "./local",
        "/sandbox/file..txt",
        "/sandbox/.../file",
        "C:\\sandbox\\file..txt",
    ],
)
def test_non_parent_dot_names_remain_valid(path: str) -> None:
    assert has_no_parent_segments(path)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/safe/%2e%2e/escape",
        "https://example.test/safe/%2E%2E/escape",
        "https://example.test/safe/%2e%2E/escape",
    ],
)
def test_encoded_url_parent_segments_are_rejected(url: str) -> None:
    assert not url_has_no_parent_segments(url)


@pytest.mark.parametrize(
    "url",
    [
        # Encoded separator after literal dots: one unquote yields "/../".
        "https://example.test/safe/..%2f..%2fescape",
        "https://example.test/safe/..%2F..%2Fescape",
        "https://example.test/safe/..%5cescape",
        "https://example.test/safe/..%5Cescape",
        # Double-encoded dots.
        "https://example.test/safe/%252e%252e/escape",
        # A trailing ".." segment terminated by the query or fragment marker
        # rather than by a separator.
        "https://example.test/safe/..?x=1",
        "https://example.test/safe/..#frag",
    ],
)
def test_encoded_separator_url_traversal_is_rejected(url: str) -> None:
    assert not url_has_no_parent_segments(url)


@pytest.mark.parametrize(
    "url",
    [
        # An encoded separator on the *opening* side of a dot segment. One
        # decode yields "//../" and "/\\..\\" respectively.
        "https://example.test/safe/%2f../escape",
        "https://example.test/safe/%2F../escape",
        "https://example.test/safe/%5c..\\escape",
        "https://example.test/safe/%5C..\\escape",
        "https://example.test/safe/%2f..",
    ],
)
def test_encoded_separator_before_dots_is_rejected(url: str) -> None:
    assert not url_has_no_parent_segments(url)


@pytest.mark.parametrize(
    "url",
    [
        # A dangerous token synthesised from encoded hex digits, so neither a
        # literal "%2e"/"%2f"/"%5c"/"%25" nor a literal ".." appears in the
        # source. Each survives two standard unquote passes into a traversal:
        #   "%%32%65%%32%65" -> "%2e%2e" -> ".."
        "https://example.test/safe/%%32%65%%32%65/escape",
        #   "%2%65" -> "%2e" -> "."   (the "e" arrives from %65)
        "https://example.test/safe/%2%65%2%65/escape",
        #   "%%32e" -> "%2e" -> "."   (the "2" arrives from %32)
        "https://example.test/safe/%%32e%%32e/escape",
        #   "%%32%66" -> "%2f" -> "/" (encoded slash, opening boundary)
        "https://example.test/safe/%%32%66../escape",
        #   "%%35%63" -> "%5c" -> "\\" (encoded backslash, opening boundary)
        "https://example.test/safe/%%35%63..\\escape",
    ],
)
def test_double_encoded_traversal_synthesis_is_rejected(url: str) -> None:
    assert not url_has_no_parent_segments(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/safe/child",
        "https://example.test/safe/file..txt",
        "https://example.test/safe/child?q=a.b",
        "https://example.test/safe/child#section",
        # Percent-encodings whose decoded bytes cannot spell a traversal token
        # stay acceptable, so the blocklist does not refuse encoding outright.
        "https://example.test/safe/child?q=a%20b",
        "https://example.test/safe/child?q=a%2Cb",
    ],
)
def test_ordinary_urls_remain_valid(url: str) -> None:
    assert url_has_no_parent_segments(url)


@pytest.mark.parametrize(
    "path",
    [
        # Windows reads a drive delimiter as a component boundary, so these are
        # real parent references even with no separator before the dots.
        "C:..",
        "C:../escape",
        "C:..\\escape",
        "D:..",
    ],
)
def test_drive_relative_parent_segments_are_rejected(path: str) -> None:
    assert not has_no_parent_segments(path)


def test_only_write_under_rejects_parent_traversal() -> None:
    result = _prove("""
@clauz3.guarantee(fs.only_write_under("/tmp/sandbox"))
def main() -> None:
    write_file("/tmp/sandbox/../../etc/passwd", "pwned")
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_never_write_under_rejects_parent_traversal() -> None:
    result = _prove("""
@clauz3.guarantee(fs.never_write_under("/etc"))
def main() -> None:
    write_file("/tmp/../etc/passwd", "pwned")
""")
    assert result.proof.conclusion is Conclusion.FAIL


def test_ordinary_child_path_still_proves() -> None:
    result = _prove("""
@clauz3.guarantee(fs.only_write_under("/tmp/sandbox"))
def main() -> None:
    write_file("/tmp/sandbox/out.txt", "safe")
""")
    assert result.ok


def test_traversal_through_a_helper_is_still_rejected() -> None:
    """These preconditions must survive ordinary helper indirection.

    ``no_guarantees()`` cannot fail, so the only thing that can refute this
    program is ``write_file``'s own ``has_no_parent_segments`` precondition.
    Before callee obligations were propagated across the call boundary (#102)
    the module-level helper form proved ``OK`` while the inline form failed,
    which made every path precondition here bypassable by a one-line wrapper.
    """
    inline = _prove("""
@clauz3.guarantee(fs.no_guarantees())
def main() -> None:
    write_file("/tmp/sandbox/../../etc/passwd", "pwned")
""")
    via_helper = _prove("""
def go() -> None:
    write_file("/tmp/sandbox/../../etc/passwd", "pwned")


@clauz3.guarantee(fs.no_guarantees())
def main() -> None:
    go()
""")
    assert inline.proof.conclusion is Conclusion.FAIL
    assert via_helper.proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize(
    ("domain", "effect_import", "call"),
    [
        (
            "editor",
            "from tools.editor.trusted.effects import edit_file",
            'edit_file("/repo/out/../escape", "x")',
        ),
        (
            "coreutils",
            "from tools.coreutils.trusted.effects import remove",
            'remove("/repo/out/../escape")',
        ),
        (
            "find",
            "from tools.find.trusted.effects import find",
            'find("/repo/out/../escape", [])',
        ),
        (
            "grep",
            "from tools.grep.trusted.effects import grep",
            'grep("needle", "/repo/out/../escape")',
        ),
        (
            "rsync",
            "from tools.rsync.trusted.effects import rsync",
            'rsync("/repo/src", "/repo/out/../escape", False, [])',
        ),
        (
            "git",
            "from tools.git.trusted.effects import git",
            'git("status", [], "/repo/out/../escape")',
        ),
        (
            "web_fetch",
            "from tools.web_fetch.trusted.effects import fetch_url",
            'fetch_url("https://example.test/safe/../escape")',
        ),
    ],
)
def test_other_stdlib_path_boundaries_reject_parent_traversal(
    domain: str,
    effect_import: str,
    call: str,
) -> None:
    domain_root = ROOT / f"src/clauz3/stdlib/{domain}"
    trusted_root = domain_root / f"tools/{domain}/trusted"
    results = prove_text(
        f"""\
import clauz3
{effect_import}


def main() -> None:
    {call}
""",
        # find and grep import tools.filesystem, so its domain must be on the
        # path; an earlier test used to leave it cached (issue #111).
        import_paths=[domain_root, FILESYSTEM_TRUSTED.parents[2]],
        trusted_roots=[trusted_root],
    )
    assert len(results) == 1
    assert results[0].proof.conclusion is Conclusion.FAIL


def test_database_path_boundary_rejects_parent_traversal() -> None:
    domain_root = ROOT / "src/clauz3/stdlib/db"
    trusted_root = domain_root / "tools/db/trusted"
    results = prove_text(
        """\
import clauz3
from tools.db.trusted.effects import read_rows


class Item(clauz3.Row):
    name: str


def main() -> None:
    read_rows(Item, "/repo/data/../escape", "items", {})
""",
        import_paths=[domain_root],
        trusted_roots=[trusted_root],
    )
    assert len(results) == 1
    assert results[0].proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize(
    "url",
    [
        # Encoded separator closing the dot segment...
        "https://example.test/safe/..%2f..%2fescape",
        "https://example.test/safe/..%5cescape",
        # ...and opening it, which is the boundary side the first fix missed.
        "https://example.test/safe/%2f../escape",
        "https://example.test/safe/%5c..\\escape",
        "https://example.test/safe/%252e%252e/escape",
        "https://example.test/safe/..?x=1",
    ],
)
def test_encoded_url_traversal_is_refused_symbolically(url: str) -> None:
    """The predicate must be refutable by the solver, not only in Python.

    ``url_has_no_parent_segments`` is only load-bearing if the prover can
    evaluate it, so exercise it through ``fetch_url``'s precondition.
    """
    domain_root = ROOT / "src/clauz3/stdlib/web_fetch"
    trusted_root = domain_root / "tools/web_fetch/trusted"
    results = prove_text(
        f"""\
import clauz3
from tools.web_fetch.trusted.effects import fetch_url


def main() -> None:
    fetch_url({url!r})
""",
        import_paths=[domain_root],
        trusted_roots=[trusted_root],
    )
    assert len(results) == 1
    assert results[0].proof.conclusion is Conclusion.FAIL


@pytest.mark.parametrize(
    "table",
    [
        # _table_file() interpolates `table` into `root / f"{table}.csv"`, so a
        # traversal escapes the directory that `path` names...
        "../../escape",
        "..\\..\\escape",
        "sub/../../escape",
        "..",
        ".",
        # ...an absolute value replaces the root outright, because
        # Path("/repo/data") / "/tmp/escape" is "/tmp/escape"...
        "/tmp/escape",
        "\\tmp\\escape",
        # ...and a bare separator is enough to reach a subdirectory.
        "sub/items",
        # A drive-qualified name discards the root the same way an absolute one
        # does: PureWindowsPath("C:/repo/data") / "D:items.csv" is "D:items.csv".
        "D:items",
        "C:items",
        "C:",
    ],
)
def test_table_names_that_leave_their_directory_are_rejected(table: str) -> None:
    assert not is_single_path_component(table)


@pytest.mark.parametrize("table", ["items", "items.v2", "items-2024", "..items"])
def test_ordinary_table_names_remain_valid(table: str) -> None:
    assert is_single_path_component(table)


def test_drive_qualified_table_cannot_escape_the_validated_root() -> None:
    """The join must stay inside the directory that ``path`` validated."""
    root = PureWindowsPath("C:/repo/data")
    assert (root / "items.csv").parent == root
    assert (root / "D:items.csv").parent != root
    assert not is_single_path_component("D:items")


@pytest.mark.parametrize("path", ["C:../escape", "C:.."])
def test_drive_relative_traversal_is_refused_symbolically(path: str) -> None:
    """The drive-boundary rule must be refutable by the solver, not only in Python."""
    results = prove_text(
        HEADER
        + f"""
@clauz3.guarantee(fs.no_guarantees())
def main() -> None:
    write_file({path!r}, "pwned")
""",
        import_paths=[FILESYSTEM_ROOT],
        trusted_roots=[FILESYSTEM_TRUSTED],
    )
    assert len(results) == 1
    assert results[0].proof.conclusion is Conclusion.FAIL


def test_parent_segment_fast_path_preserves_component_semantics() -> None:
    """Check mixed separators and embedded dots against an independent tokenizer."""
    import itertools
    import re

    for length in range(7):
        for characters in itertools.product("./\\:a", repeat=length):
            path = "".join(characters)
            expected = ".." not in re.split(r"[/\\]", path) and ":.." not in path
            assert has_no_parent_segments(path) == expected, repr(path)

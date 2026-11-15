# Filesystem edit and search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `edit_file` (exact string replacement) and `search_files` (recursive ripgrep search returning typed rows) to `stdlib:filesystem`, covered by the existing filesystem contracts.

**Architecture:** Both are new `@clauz3.trusted` effects in the bundle's `effects.py`. They declare existing events with `@performs` (`Read` + `WriteText` for edits, `RecursiveRead` for searches), so every current contract applies with no contract changes. `search_files` returns `list[Match]`, a `clauz3.Row` declared in a new `schemas.py`, and bounds its result length by its `max_results` argument with an argument-aware `@clauz3.ensures`.

**Tech Stack:** Python 3.11, clauz3 trusted-layer decorators (`clauz3.trusted`, `clauz3.requires`, `clauz3.ensures`, `clauz3.events.performs`), deal, pytest, ripgrep (`rg`) 13+.

**Spec:** `docs/superpowers/specs/2026-11-14-filesystem-edit-search-design.md`

**Work in:** `/Users/cjm/orca/workspaces/clauz3/cz3-fs-edit-search` (branch `cmungall/cz3-fs-edit-search`). Run every command from that directory.

---

## Background for the implementer

- **Trusted layer.** `src/clauz3/stdlib/filesystem/tools/filesystem/trusted/` holds `events.py` (event classes), `effects.py` (the side-effecting functions) and `contracts.py` (policies over events). The prover never runs effect bodies; it records each call as the events listed in `@performs`, proves `@clauz3.requires` preconditions, and checks contracts. Bodies run only under `clauz3 run`.
- **Decorator order** in this file is fixed: `@clauz3.requires(...)` (one or more), optional `@clauz3.ensures(...)`, `@deal.has(...)`, `@clauz3.trusted`, then `@performs(...)` directly above `def`.
- **Precondition lambdas** must declare *all* the function's parameters with the same names and defaults (`lambda path, old, new, replace_all=False: ...`). The argument-aware `ensures` form declares all parameters plus `result`.
- **`@performs` builders** take any subset of the function's parameters; each event field must be filled by a parameter of the same type, never a literal or a defaulted parameter.
- **Proof cases** live in `src/clauz3/stdlib/filesystem/tests/cases/`. The bundle Justfile proves every `*_pass.py` (must prove) and `*_fail.py` (must not) automatically, so new cases need no Justfile edit. Prove one case with:
  `uv run clauz3 prove --trusted-root src/clauz3/stdlib/filesystem/tools/filesystem/trusted --import-root src/clauz3/stdlib/filesystem src/clauz3/stdlib/filesystem/tests/cases/NAME.py`
  Exit 0 and `proved!` means it proved; non-zero means it did not.
- **Runtime tests** live in the repo's `tests/` and load the real effects module through the `filesystem_effects` fixture in `tests/conftest.py`.
- **ripgrep behaviour that matters:** `--json` emits one JSON object per line; matches have `"type": "match"` with `data.path.text`, `data.line_number` and `data.lines.text` (which keeps its `\n` or `\r\n`). Non-UTF-8 values arrive as `{"bytes": "<base64>"}` instead of `{"text": ...}`. Exit status 0 = matches, 1 = no matches, 2 = error. `.gitignore` is only honoured inside a git repository.
- Project rules: pytest style, no swallowed exceptions, `just check` (ruff + strict mypy) must pass.

## File structure

| File | Change | Responsibility |
|---|---|---|
| `src/clauz3/stdlib/filesystem/tools/filesystem/trusted/schemas.py` | Create | `Match` row returned by `search_files` |
| `src/clauz3/stdlib/filesystem/tools/filesystem/trusted/effects.py` | Modify | `edit_file`, `search_files`, `_rg_text` helper; module docstring |
| `src/clauz3/stdlib/filesystem/tools/filesystem/clauz3.yaml` | Modify | `rg` tool requirement; summary mentions editing |
| `src/clauz3/stdlib/filesystem/tests/cases/edit_*.py`, `search_*.py` | Create | Proof cases |
| `tests/test_edit_file_runtime.py` | Create | `edit_file` behaviour on real temp files |
| `tests/test_search_files_runtime.py` | Create | `search_files` behaviour with real `rg` |
| `src/clauz3/stdlib/filesystem/README.md` | Modify | Effects list, "Edit code" section, caveats |
| `docs/stdlib/filesystem.md` | Modify | Include `schemas.py` source |

---

### Task 1: `edit_file` runtime behaviour

**Files:**
- Create: `tests/test_edit_file_runtime.py`
- Modify: `src/clauz3/stdlib/filesystem/tools/filesystem/trusted/effects.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_edit_file_runtime.py`:

```python
"""edit_file replaces exact text in place and never half-writes a file."""

import os
import stat
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import deal
import pytest

Edit = Callable[..., None]


@pytest.fixture
def edit_file(filesystem_effects: ModuleType) -> Edit:
    return cast(Edit, filesystem_effects.edit_file)


def test_replaces_a_unique_occurrence(edit_file: Edit, tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x = 1\ny = 2\n")
    edit_file(str(target), "y = 2", "y = 3")
    assert target.read_text() == "x = 1\ny = 3\n"


@pytest.mark.parametrize("text,found", [("x = 1\n", 0), ("y\ny\n", 2)])
def test_ambiguous_or_missing_text_leaves_file_unchanged(
    edit_file: Edit, tmp_path: Path, text: str, found: int
) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(text.encode())
    with pytest.raises(ValueError, match=f"found {found}"):
        edit_file(str(target), "y", "z")
    assert target.read_bytes() == text.encode()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.py"]


def test_replace_all_replaces_every_occurrence(
    edit_file: Edit, tmp_path: Path
) -> None:
    target = tmp_path / "a.py"
    target.write_text("y\ny\n")
    edit_file(str(target), "y", "z", replace_all=True)
    assert target.read_text() == "z\nz\n"


def test_replace_all_still_requires_an_occurrence(
    edit_file: Edit, tmp_path: Path
) -> None:
    target = tmp_path / "a.py"
    target.write_text("x\n")
    with pytest.raises(ValueError, match="found 0"):
        edit_file(str(target), "y", "z", replace_all=True)


def test_preserves_crlf_line_endings(edit_file: Edit, tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"one\r\ntwo\r\n")
    edit_file(str(target), "two", "three")
    assert target.read_bytes() == b"one\r\nthree\r\n"


def test_preserves_permission_bits(edit_file: Edit, tmp_path: Path) -> None:
    target = tmp_path / "run.sh"
    target.write_text("echo hi\n")
    target.chmod(0o750)
    edit_file(str(target), "hi", "bye")
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o750


def test_missing_file_is_not_created(edit_file: Edit, tmp_path: Path) -> None:
    target = tmp_path / "missing.py"
    with pytest.raises(FileNotFoundError):
        edit_file(str(target), "a", "b")
    assert not target.exists()


@pytest.mark.parametrize("old,new", [("", "x"), ("same", "same")])
def test_preconditions_reject_empty_or_unchanged_text(
    edit_file: Edit, tmp_path: Path, old: str, new: str
) -> None:
    target = tmp_path / "a.py"
    target.write_text("same\n")
    with pytest.raises(deal.PreContractError):
        edit_file(str(target), old, new)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_edit_file_runtime.py -q`
Expected: every test FAILS with `AttributeError: module 'filesystem_effects' has no attribute 'edit_file'`.

- [ ] **Step 3: Implement `edit_file`**

In `effects.py`, add `import tempfile` to the standard-library imports (keep them sorted: `os`, `shutil`, `subprocess`, `tempfile`). Then add this function directly after `append_file` (still in the `# -- writing` section):

```python
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

    ``old`` must occur exactly once unless ``replace_all`` is true, in which
    case every occurrence is replaced and at least one must exist. Otherwise
    ``ValueError`` reports how many were found and the file is unchanged. The
    file must exist; this never creates one. Line endings and permission bits
    are preserved, and the new contents replace the file atomically.

    Contracts see a read of ``path`` and a text write of ``new`` to ``path``.
    ``must_not_write`` and ``write_length_at_most`` therefore check only the
    inserted text, not the resulting file.
    """
    target = Path(path)
    with target.open(encoding="utf-8", newline="") as stream:
        text = stream.read()
    found = text.count(old)
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
        os.unlink(temporary)
        raise
```

Also add `import stat` to the standard-library imports (sorted: `os`, `shutil`, `stat`, `subprocess`, `tempfile`).

Note on the error message: both branches must contain `found N` so the parametrized test matches. Check: zero → "found 0 occurrences"; two → "found 2;". Both contain `found 0` / `found 2`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_edit_file_runtime.py -q`
Expected: `10 passed`.

- [ ] **Step 5: Lint the trusted module**

Run: `uv run clauz3 lint src/clauz3/stdlib/filesystem/tools/filesystem/trusted/effects.py`
Expected: no findings. If lint reports a deal marker the body needs but `@deal.has` lacks, add exactly the marker it names (for example `"io"`) and rerun Steps 4–5.

- [ ] **Step 6: Commit**

```bash
git add tests/test_edit_file_runtime.py src/clauz3/stdlib/filesystem/tools/filesystem/trusted/effects.py
git commit -m "Add filesystem edit_file for exact in-place replacement

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `edit_file` proof cases

**Files:**
- Create: `src/clauz3/stdlib/filesystem/tests/cases/edit_only_writes_files_under_pass.py`
- Create: `src/clauz3/stdlib/filesystem/tests/cases/edit_only_writes_files_under_fail.py`
- Create: `src/clauz3/stdlib/filesystem/tests/cases/edit_read_only_fail.py`
- Create: `src/clauz3/stdlib/filesystem/tests/cases/edit_never_read_under_fail.py`
- Create: `src/clauz3/stdlib/filesystem/tests/cases/edit_empty_old_fail.py`
- Create: `src/clauz3/stdlib/filesystem/tests/cases/edit_unchanged_text_fail.py`

- [ ] **Step 1: Write the cases**

`edit_only_writes_files_under_pass.py`:

```python
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(fs.only_writes_files_under(clauz3.DirectoryPath("src")))
def main() -> None:
    edit_file("src/app.py", "DEBUG = True", "DEBUG = False")
    edit_file("src/lib/util.py", "old_name", "new_name", replace_all=True)
```

`edit_only_writes_files_under_fail.py`:

```python
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(fs.only_writes_files_under(clauz3.DirectoryPath("src")))
def main() -> None:
    edit_file("src/app.py", "DEBUG = True", "DEBUG = False")
    edit_file("setup.cfg", "a", "b")
```

`edit_read_only_fail.py`:

```python
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(fs.read_only())
def main() -> None:
    edit_file("src/app.py", "a", "b")
```

`edit_never_read_under_fail.py` (an edit reads the file first):

```python
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(fs.never_read_under("/home/user/.ssh"))
def main() -> None:
    edit_file("/home/user/.ssh/config", "Host a", "Host b")
```

`edit_empty_old_fail.py`:

```python
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(fs.only_writes_files_under(clauz3.DirectoryPath("src")))
def main() -> None:
    edit_file("src/app.py", "", "prefix")
```

`edit_unchanged_text_fail.py`:

```python
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file

import clauz3


@clauz3.guarantee(fs.only_writes_files_under(clauz3.DirectoryPath("src")))
def main() -> None:
    edit_file("src/app.py", "same", "same")
```

- [ ] **Step 2: Prove each case**

Run (one line per case):

```bash
P="uv run clauz3 prove --trusted-root src/clauz3/stdlib/filesystem/tools/filesystem/trusted --import-root src/clauz3/stdlib/filesystem"
C=src/clauz3/stdlib/filesystem/tests/cases
for f in $C/edit_*.py; do $P $f >/dev/null 2>&1; echo "$? $(basename $f)"; done
```

Expected: `0 edit_only_writes_files_under_pass.py` and `1` for every `_fail.py`. For each `_fail.py`, also run it alone and confirm the output names the intended reason: `failed guarantee` for the three contract cases, `failed assertion` (precondition) for `edit_empty_old_fail.py` and `edit_unchanged_text_fail.py`. A different failure reason means the case is not testing what its name says; fix the case.

- [ ] **Step 3: Run the bundle suite**

Run: `just -f src/clauz3/stdlib/filesystem/tests/Justfile test`
Expected: exits 0.

- [ ] **Step 4: Commit**

```bash
git add src/clauz3/stdlib/filesystem/tests/cases/edit_*.py
git commit -m "Add proof cases for filesystem edit_file

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `Match` row and `search_files` runtime behaviour

**Files:**
- Create: `src/clauz3/stdlib/filesystem/tools/filesystem/trusted/schemas.py`
- Create: `tests/test_search_files_runtime.py`
- Modify: `src/clauz3/stdlib/filesystem/tools/filesystem/trusted/effects.py`

- [ ] **Step 1: Create the schema**

`schemas.py`:

```python
import clauz3


class Match(clauz3.Row):
    """One matching line, as returned by ``search_files``.

    ``path`` is the file's path as ripgrep reports it under the searched
    ``path`` argument. ``line_number`` is one-based. ``text`` is the line
    without its terminator.
    """

    path: str
    line_number: int
    text: str
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_search_files_runtime.py`:

```python
"""search_files runs ripgrep and returns bounded, typed matches."""

import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import deal
import pytest

Search = Callable[[str, str, str, int], list[Any]]


@pytest.fixture
def search_files(filesystem_effects: ModuleType) -> Search:
    return cast(Search, filesystem_effects.search_files)


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("import os\nTODO = 1\n")
    (tmp_path / "src/notes.txt").write_text("TODO later\r\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build/gen.py").write_text("TODO generated\n")
    (tmp_path / ".gitignore").write_text("build/\n")
    # ripgrep honours .gitignore only inside a git repository.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def rows(matches: list[Any]) -> list[tuple[str, int, str]]:
    return sorted((m.path, m.line_number, m.text) for m in matches)


def test_returns_path_line_number_and_text(search_files: Search, tree: Path) -> None:
    assert rows(search_files("TODO", "src", "", 10)) == [
        ("src/app.py", 2, "TODO = 1"),
        ("src/notes.txt", 1, "TODO later"),
    ]


def test_glob_restricts_file_names(search_files: Search, tree: Path) -> None:
    assert rows(search_files("TODO", "src", "*.py", 10)) == [
        ("src/app.py", 2, "TODO = 1"),
    ]


def test_respects_gitignore(search_files: Search, tree: Path) -> None:
    assert [m.path for m in search_files("generated", ".", "", 10)] == []


def test_stops_at_max_results(search_files: Search, tree: Path) -> None:
    assert len(search_files("TODO", "src", "", 1)) == 1


def test_no_matches_returns_empty_list(search_files: Search, tree: Path) -> None:
    assert search_files("absent-text", "src", "", 10) == []


def test_pattern_starting_with_dash_is_a_pattern(
    search_files: Search, tree: Path
) -> None:
    (tree / "src/flags.txt").write_text("use -v here\n")
    assert rows(search_files("-v", "src", "", 10)) == [
        ("src/flags.txt", 1, "use -v here"),
    ]


def test_regex_patterns(search_files: Search, tree: Path) -> None:
    assert rows(search_files(r"^import \w+$", "src", "", 10)) == [
        ("src/app.py", 1, "import os"),
    ]


def test_ripgrep_errors_propagate(search_files: Search, tree: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        search_files("(", "src", "", 10)


@pytest.mark.parametrize(
    "pattern,max_results", [("", 10), ("TODO", 0), ("TODO", 1001)]
)
def test_preconditions(
    search_files: Search, tree: Path, pattern: str, max_results: int
) -> None:
    with pytest.raises(deal.PreContractError):
        search_files(pattern, "src", "", max_results)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_search_files_runtime.py -q`
Expected: every test FAILS with `AttributeError: module 'filesystem_effects' has no attribute 'search_files'`.

- [ ] **Step 4: Implement `search_files`**

In `effects.py`:

1. Add `import base64` and `import json` to the standard-library imports (sorted: `base64`, `json`, `os`, `shutil`, `stat`, `subprocess`, `tempfile`).
2. Add `from tools.filesystem.trusted.schemas import Match` directly after the `from tools.filesystem.trusted.events import (...)` block.
3. `RecursiveRead` is already imported from `events`; no change there.
4. Add this helper and effect directly after `find` (end of the reading/search section, before `# -- writing`):

```python
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
    lambda pattern, path, glob, max_results: max_results >= 1
    and max_results <= 1000,
    message="max_results must be between 1 and 1000",
)
@clauz3.ensures(
    lambda pattern, path, glob, max_results, result: len(result) <= max_results
)
@deal.has("read", "io", "syscall")
@clauz3.trusted
@performs(lambda path: RecursiveRead(path))
def search_files(pattern: str, path: str, glob: str, max_results: int) -> list[Match]:
    """Return up to ``max_results`` lines under ``path`` matching ``pattern``.

    ``pattern`` is a ripgrep (Rust) regular expression; the search is recursive.
    ``glob`` limits file names, for example ``*.py``; an empty string searches
    all files. ``max_results`` must be between 1 and 1000. Each result is a
    ``Match`` with the file path, one-based line number and line text.

    ripgrep's defaults apply: ``.gitignore``d (inside a git repository),
    hidden and binary files are skipped, and symlinks are not followed. No
    matches returns ``[]``; ripgrep errors, such as an invalid pattern, raise.
    Contracts see a recursive read of ``path``.
    """
    # Prefix relative roots so rg cannot parse the path as an option, and
    # strip the prefix again from reported paths.
    prefixed = not (path == "." or os.path.isabs(path) or path.startswith("./"))
    root = "./" + path if prefixed else path
    command = ["rg", "--json", "-e", pattern]
    if glob:
        command += ["--glob", glob]
    command += ["--", root]
    completed = subprocess.run(  # noqa: S603
        command, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT_SECONDS
    )
    if completed.returncode not in (0, 1):
        raise subprocess.CalledProcessError(
            completed.returncode, command, completed.stdout, completed.stderr
        )
    matches: list[Match] = []
    for line in completed.stdout.splitlines():
        event = json.loads(line)
        if event["type"] != "match":
            continue
        data = event["data"]
        found = _rg_text(data["path"])
        matches.append(
            Match(
                path=found.removeprefix("./") if prefixed else found,
                line_number=data["line_number"],
                text=_rg_text(data["lines"]).removesuffix("\n").removesuffix("\r"),
            )
        )
        if len(matches) == max_results:
            break
    return matches
```

5. Update the module docstring's second paragraph: replace the sentence `` ``find`` is the exception: it runs the ``find`` binary with an allowlisted argument grammar.`` with `` ``find`` and ``search_files`` are the exceptions: they run the ``find`` and ``rg`` binaries with fixed argument vectors.``

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_search_files_runtime.py tests/test_edit_file_runtime.py tests/test_find_runtime.py -q`
Expected: all pass (`search_files`: 11 tests; `edit_file`: 10 tests; `find`'s existing tests unchanged).

- [ ] **Step 6: Lint the trusted module**

Run: `uv run clauz3 lint src/clauz3/stdlib/filesystem/tools/filesystem/trusted/effects.py`
Expected: no findings. If lint names a missing deal marker, add exactly that marker and rerun Steps 5–6.

- [ ] **Step 7: Commit**

```bash
git add src/clauz3/stdlib/filesystem/tools/filesystem/trusted/schemas.py src/clauz3/stdlib/filesystem/tools/filesystem/trusted/effects.py tests/test_search_files_runtime.py
git commit -m "Add filesystem search_files returning typed ripgrep matches

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `search_files` proof cases

**Files:**
- Create: `src/clauz3/stdlib/filesystem/tests/cases/search_never_read_under_pass.py`
- Create: `src/clauz3/stdlib/filesystem/tests/cases/search_never_read_under_fail.py`
- Create: `src/clauz3/stdlib/filesystem/tests/cases/search_parent_never_read_under_fail.py`
- Create: `src/clauz3/stdlib/filesystem/tests/cases/search_then_edit_pass.py`
- Create: `src/clauz3/stdlib/filesystem/tests/cases/search_then_edit_count_fail.py`
- Create: `src/clauz3/stdlib/filesystem/tests/cases/search_max_results_fail.py`

- [ ] **Step 1: Write the cases**

`search_never_read_under_pass.py`:

```python
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import search_files

import clauz3


@clauz3.guarantee(fs.never_read_under("/home/user/.ssh"))
def main() -> None:
    for match in search_files("TODO", "src", "*.py", 20):
        print(match.path, match.line_number, match.text)
```

`search_never_read_under_fail.py`:

```python
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import search_files

import clauz3


@clauz3.guarantee(fs.never_read_under("/home/user/.ssh"))
def main() -> None:
    search_files("BEGIN", "/home/user/.ssh", "", 20)
```

`search_parent_never_read_under_fail.py` (a recursive search of a parent covers the forbidden directory):

```python
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import search_files

import clauz3


@clauz3.guarantee(fs.never_read_under("/home/user/.ssh"))
def main() -> None:
    search_files("BEGIN", "/home/user", "", 20)
```

`search_then_edit_pass.py`:

```python
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file, search_files

import clauz3


@clauz3.guarantee(fs.only_writes_files_under(clauz3.DirectoryPath("src")))
@clauz3.guarantee(fs.writes_at_most(10))
def main() -> None:
    for match in search_files("old_name", "src", "*.py", 10):
        print(match.path, match.line_number)
        edit_file("src/app.py", "old_name", "new_name")
```

`search_then_edit_count_fail.py` (same program, one write too few allowed):

```python
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file, search_files

import clauz3


@clauz3.guarantee(fs.only_writes_files_under(clauz3.DirectoryPath("src")))
@clauz3.guarantee(fs.writes_at_most(9))
def main() -> None:
    for match in search_files("old_name", "src", "*.py", 10):
        print(match.path, match.line_number)
        edit_file("src/app.py", "old_name", "new_name")
```

`search_max_results_fail.py`:

```python
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import search_files

import clauz3


@clauz3.guarantee(fs.never_read_under("/home/user/.ssh"))
def main() -> None:
    search_files("TODO", "src", "", 5000)
```

- [ ] **Step 2: Prove each case**

```bash
P="uv run clauz3 prove --trusted-root src/clauz3/stdlib/filesystem/tools/filesystem/trusted --import-root src/clauz3/stdlib/filesystem"
C=src/clauz3/stdlib/filesystem/tests/cases
for f in $C/search_*.py; do $P $f >/dev/null 2>&1; echo "$? $(basename $f)"; done
```

Expected: `0` for the two `_pass.py` files, `1` for the four `_fail.py` files. Run each `_fail.py` alone and confirm: `failed guarantee` for the two `never_read_under` cases and the count case; `failed assertion` for `search_max_results_fail.py`.

The edit loops use literal `old`/`new` arguments on purpose: the prover cannot know a row's `text` is non-empty or different from `new`, so `edit_file(..., match.text, ...)` would fail its preconditions. These cases test the count bound from `max_results`.

- [ ] **Step 3: Run the bundle suite**

Run: `just -f src/clauz3/stdlib/filesystem/tests/Justfile test`
Expected: exits 0.

- [ ] **Step 4: Commit**

```bash
git add src/clauz3/stdlib/filesystem/tests/cases/search_*.py
git commit -m "Add proof cases for filesystem search_files

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Manifest and documentation

**Files:**
- Modify: `src/clauz3/stdlib/filesystem/tools/filesystem/clauz3.yaml`
- Modify: `src/clauz3/stdlib/filesystem/README.md:124-150` (Effects section) and a new section after it
- Modify: `docs/stdlib/filesystem.md`

- [ ] **Step 1: Update the manifest**

Replace the whole `clauz3.yaml` with:

```yaml
schema_version: 1
name: filesystem
title: Files and folders
summary: >-
  Read, search, list, write, edit, copy, move, delete and chmod files, with contracts over what each tool does to the filesystem.
description: |
  Work with local files and folders: read text, search contents, list entries,
  edit code in place, and make approved changes. Contracts distinguish exact
  file names from folder boundaries and can restrict reads, writes, and
  operation counts.
requirements:
  tools:
    - name: find
      description: The find executable is needed for filesystem.find calls.
      instructions: Install find and make it available on the PATH used by Clauz3.
    - name: rg
      description: ripgrep is needed for filesystem.search_files calls.
      instructions: Install ripgrep (for example, brew install ripgrep) and make rg available on the PATH used by Clauz3.
      documentation: https://github.com/BurntSushi/ripgrep#installation
```

Then validate: `uv run python -c "from clauz3.libraries.manifest import main; main()"`
Expected: `Validated N library manifests` with no error. If the validator rejects the `documentation` key on a tool requirement, remove that line (the gmail bundle's `gog` requirement uses it, so it should be accepted).

- [ ] **Step 2: Update the README effects list**

In `src/clauz3/stdlib/filesystem/README.md`, in the `## Effects` list, insert after the `grep` bullet:

```markdown
- `search_files(pattern, path, glob, max_results) -> list[Match]`: search
  recursively under `path` with ripgrep. `pattern` is a regular expression,
  `glob` limits file names (`""` for all), and at most `max_results` (1 to
  1000) `Match` rows are returned, each with `path`, `line_number` and `text`.
  ripgrep's defaults apply: `.gitignore`d (inside a git repository), hidden
  and binary files are skipped. Needs `rg` on the PATH.
```

and after the `append_file` bullet:

```markdown
- `edit_file(path, old, new, replace_all=False)`: replace the exact text `old`
  with `new` in an existing file. `old` must occur exactly once unless
  `replace_all` is true; otherwise `ValueError` and the file is unchanged.
  Line endings and permission bits are preserved.
```

- [ ] **Step 3: Add an "Edit code" section**

Insert this section immediately before `## Contracts` in the README:

````markdown
## Edit code

`search_files` and `edit_file` cover the common edit loop: find the lines,
then change them in place. Both perform existing events, so path contracts
apply as to any other tool. This program can only change files under `src/`:

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file, search_files


@clauz3.guarantee(fs.only_writes_files_under(clauz3.DirectoryPath("src")))
@clauz3.guarantee(fs.writes_at_most(1))
def main() -> None:
    for match in search_files("DEBUG = True", "src", "*.py", 5):
        print(match.path, match.line_number, match.text)
    edit_file("src/settings.py", "DEBUG = True", "DEBUG = False")
```

An edit is a read of the file and a text write of `new`. `must_not_write` and
`write_length_at_most` check that inserted text, not the resulting file:
deleting text can join its neighbours into a forbidden string, and that is
not detected. A search is a recursive read of its `path`, so
`never_read_under` refuses a search of any parent of the forbidden directory.
````

- [ ] **Step 4: Include the schema source on the docs page**

In `docs/stdlib/filesystem.md`, after the `events.py` include line, add:

```markdown
{{ include_file("src/clauz3/stdlib/filesystem/tools/filesystem/trusted/schemas.py") }}
```

- [ ] **Step 5: Prove the README example**

Write the README program to a scratch file and prove it:

```bash
mkdir -p /tmp/claude-fsdoc && cat > /tmp/claude-fsdoc/edit_example.py <<'PY'
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import edit_file, search_files


@clauz3.guarantee(fs.only_writes_files_under(clauz3.DirectoryPath("src")))
@clauz3.guarantee(fs.writes_at_most(1))
def main() -> None:
    for match in search_files("DEBUG = True", "src", "*.py", 5):
        print(match.path, match.line_number, match.text)
    edit_file("src/settings.py", "DEBUG = True", "DEBUG = False")
PY
uv run clauz3 prove --trusted-root src/clauz3/stdlib/filesystem/tools/filesystem/trusted --import-root src/clauz3/stdlib/filesystem /tmp/claude-fsdoc/edit_example.py
```

Expected: `main: proved!`. Keep this file identical to the README example; if you change one, change both.

- [ ] **Step 6: Run the docs-related tests**

Run: `uv run pytest tests/test_library_manifests.py tests/test_authoring_skill_examples.py tests/test_libraries.py -q`
Expected: all pass. A test that snapshots the filesystem summary or effect list may need its expected text updated to include `edit`/`search_files`; update only that expected text.

- [ ] **Step 7: Commit**

```bash
git add src/clauz3/stdlib/filesystem/tools/filesystem/clauz3.yaml src/clauz3/stdlib/filesystem/README.md docs/stdlib/filesystem.md
git commit -m "Document filesystem edit_file and search_files

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Full verification

- [ ] **Step 1: Static checks**

Run: `just check`
Expected: ruff, format check, strict mypy and manifest validation all pass.

- [ ] **Step 2: Bundle suite**

Run: `just -f src/clauz3/stdlib/filesystem/tests/Justfile test`
Expected: exits 0.

- [ ] **Step 3: Full pytest**

Run: `uv run pytest -q -n auto`
Expected: all pass (about 15 minutes). Investigate any failure; do not skip or loosen tests.

- [ ] **Step 4: End-to-end check against a real tree**

Install the bundle into a scratch project and run the real bodies on a copy of a file:

```bash
S=$(mktemp -d) && uv run clauz3 install stdlib:filesystem --into $S
mkdir -p $S/src && printf 'DEBUG = True\n' > $S/src/settings.py
cd $S && git init -q && PYTHONPATH=$S/.clauz3 uv run --project /Users/cjm/orca/workspaces/clauz3/cz3-fs-edit-search python -c "
from tools.filesystem.trusted.effects import edit_file, search_files
print(search_files('DEBUG', 'src', '*.py', 5))
edit_file('src/settings.py', 'DEBUG = True', 'DEBUG = False')
print(open('src/settings.py').read())"
```

Expected: one `Match(path='src/settings.py', line_number=1, text='DEBUG = True')`, then `DEBUG = False`.

- [ ] **Step 5: Push and open a PR**

Only after the user confirms. Then:

```bash
git push -u origin cmungall/cz3-fs-edit-search
gh pr create --base main --title "Add filesystem edit_file and search_files" --body-file <body>
```

The PR body summarises the two effects, the inserted-text caveat, the `rg` requirement, and the validation results, ending with the Claude Code attribution line.

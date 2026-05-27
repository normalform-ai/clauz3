# editor

A trusted `clauz3` layer for in-place file editing — `edit_file` (full
replace) and `append_file` (append) — with path-prefix policies and
content-substring guards an agent can prove before any disk write.

Unlike the layers under `examples/`, these effects are **real**, not mocks:
`edit_file` actually overwrites and `append_file` actually appends. The
prover never runs their bodies — it records each call as a fact and proves
the contract. The bodies run only under `clauz3 run`, after a program is
proved and approved.

## Install

```bash
clauz3 install stdlib:editor
```

## Effects

Import with `from tools.editor.trusted.effects import edit_file, append_file`:

- `edit_file(path: str, new_text: str) -> None` — overwrite a UTF-8 file.
  Precondition: `path` is non-empty. Recorded under the `edit` and `write`
  markers.
- `append_file(path: str, text: str) -> None` — append UTF-8 text.
  Precondition: `path` is non-empty. Recorded under the `edit` and `write`
  markers.

Both create parent directories as needed.

Because both effects also carry the `write` marker, filesystem write policies
from `stdlib:filesystem` (e.g. `only_write_under`, `writes_at_most`) also
apply to edits — installing both layers gives you stacked enforcement
without redeclaring constraints.

## Contracts

Import with `from tools.editor.trusted import contracts as ed`:

- `ed.no_guarantees()` — explicit null contract.
- `ed.no_edits()` — no edit (replace or append) is performed.
- `ed.no_appends()` — no append is performed.
- `ed.only_edit_under(root)` — every edit path is under `root`.
- `ed.never_edit_under(prefix)` — no edit path is under `prefix`.
- `ed.edits_at_most(count)` — at most `count` edits occur.
- `ed.replace_length_at_most(max_chars)` — every full-replace fits in `max_chars`.
- `ed.append_length_at_most(max_chars)` — every append chunk fits in `max_chars`.
- `ed.must_not_replace(substring)` — no full-replace contains `substring`.
- `ed.must_not_append(substring)` — no append contains `substring`.

The `must_not_*` contracts are useful as secrets / banned-token guards
before content reaches disk.

## Example

```python
import clauz3
from tools.editor.trusted import contracts as ed
from tools.editor.trusted.effects import append_file, edit_file


@clauz3.guarantee(ed.only_edit_under("/repo/build"))
@clauz3.guarantee(ed.must_not_replace("BEGIN PRIVATE KEY"))
@clauz3.guarantee(ed.edits_at_most(10))
def main() -> None:
    edit_file("/repo/build/manifest.json", '{"version": "1.0"}')
    append_file("/repo/build/log.txt", "build complete\n")
```

## Tests

```bash
clauz3 test stdlib:editor
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not.

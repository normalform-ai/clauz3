# filesystem

A trusted `clauz3` layer for reading and writing files, with path-prefix
policies an agent can prove before any file is touched.

Unlike the layers under `examples/`, these effects are **real**, not mocks:
`read_file` and `read_lines` actually read and `write_file` actually writes. The prover never
runs their bodies — it records each call as a fact and proves the contract.
The bodies run only under `clauz3 run`, after a program is proved and approved.

## Install

```bash
clauz3 install stdlib:filesystem
```

This copies `tools/filesystem/` into your project. Then prove a program against
it:

```bash
clauz3 prove --trusted-root tools/filesystem/trusted plan.py
```

## Effects

Import from `tools.filesystem.trusted.effects`:

- `read_file(path: str) -> str` — read a UTF-8 text file. Precondition: `path`
  is non-empty and has no lexical `..` component.
- `read_lines(path: str) -> list[str]` — read UTF-8 logical lines without
  terminators, using Python `str.splitlines()`. Blank lines and a final
  unterminated line count; an empty file returns `[]`. CRLF is one separator;
  Unicode line separators are recognized. Preconditions match `read_file`.
  Decode/I/O errors propagate; the whole file is loaded into memory.
- `write_file(path: str, content: str) -> None` — write UTF-8 text, creating
  parent directories. Precondition: `path` is non-empty and has no lexical
  `..` component.

## Contracts

Import with `from tools.filesystem.trusted import contracts as fs`:

- `fs.no_guarantees()` — explicit null contract.
- `fs.read_only()` — the program performs no writes.
- `fs.no_reads()` — the program reads no files.
- `fs.only_read_under(root)` — every read path is under `root`.
- `fs.only_write_under(root)` — every write path is under `root`.
- `fs.never_read_under(prefix)` — no read path is under `prefix`.
- `fs.never_write_under(prefix)` — no write path is under `prefix`.
- `fs.writes_at_most(count)` — at most `count` writes occur.

`only_*` and `never_*` are path-prefix policies, compiled to `str.startswith`
over the symbolic call argument. The trusted effects reject parent-directory
segments first, so a path cannot satisfy a prefix and then traverse outside
it at runtime.

## Example

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_file, write_file


@clauz3.guarantee(fs.only_read_under("/repo"))
@clauz3.guarantee(fs.only_write_under("/repo/build"))
def main() -> None:
    source = read_file("/repo/src/app.py")
    write_file("/repo/build/app.txt", source)
```

Stacking guarantees confines the program to reading under `/repo` and writing
under `/repo/build`. A write to `/etc/passwd` or a read of `/home/user/.ssh/id_rsa`
would fail the proof.

## Counting lines

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import read_lines

@clauz3.guarantee(fs.only_read_under("./data/"))
@clauz3.guarantee(fs.read_only())
def main() -> None:
    print(len(read_lines("./data/input.txt")))
```

This counts logical lines, not LF bytes as `wc -l` does. For `hello` without a
terminator, the logical count is 1 and `wc -l` is 0. A trailing separator does
not add a phantom line; `hello\n\n` has two logical lines. All filesystem read
contracts cover both `read_file` and `read_lines`.

Sessions expose this as the `count-lines` recipe through `list_skills` and
`read_skill` when the installed filesystem library matches the bundled version.
After upgrading clauz3, an older copied filesystem library hides all recipes
that require it, including `read-file`, `write-and-read`, and directory-search
recipes, until that library is updated. The installed effects remain usable
through `tools()` and `run`; only the tested recipe previews are withheld.
Existing copied libraries need an operator-authorized update to gain the new
effect; updating clauz3 alone does not rewrite project libraries. For an
unmodified bundled installation, the operator can run
`clauz3 install --force stdlib:filesystem` in the project and restart the session.
`--force` overwrites the installed filesystem library; preserve any local
customizations before choosing that update.

## Tests

```bash
just -f tests/Justfile test
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not.

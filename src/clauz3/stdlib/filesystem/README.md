# filesystem

A trusted `clauz3` layer for reading, searching, listing, writing and managing
files, with path policies an agent can prove before any file is touched.

Unlike the layers under `examples/`, these effects are **real**, not mocks. The
prover never runs their bodies: it records each call as a fact and proves the
contract. The bodies run only under `clauz3 run`, after a program is proved and
approved.

## Install

```bash
clauz3 install stdlib:filesystem
```

This copies `tools/filesystem/` into your project. Then prove a program against
it:

```bash
clauz3 prove --trusted-root tools/filesystem/trusted plan.py
```

## Contracts cover events, not functions

Each effect declares, with `@performs`, the events it performs on the
filesystem (`events.py`):

| Effect | Events |
| --- | --- |
| `read_file(path)`, `read_lines(path)`, `read_line(path, line_number)` | `Read(path)` |
| `grep(pattern, path)` | `Read(path)` |
| `find(path, args)` | `List(path)` |
| `write_file(path, content)`, `append_file(path, text)` | `WriteText(path, text)` |
| `make_dir(path)`, `touch(path)` | `Create(path)` |
| `remove(path)` | `Delete(path)` |
| `copy(src, dest)` | `Read(src)`, `Write(dest)` |
| `move(src, dest)` | `Read(src)`, `Delete(src)`, `Write(dest)` |
| `chmod(path, mode)` | `Chmod(path, mode)` |

The contracts are stated over events, so one guarantee covers every effect that
performs the event. `never_read_under("/repo/PRECIOUS.md")` refuses `read_file`,
`grep` and `read_line` on that file, and a `copy` or `move` from it.
Moving the file and reading it under a new name is refused at the move.

Events form a hierarchy, and a contract over an event covers its subclasses.
`Read` and `List` are kinds of `Observe`, and `WriteText`, `Create`, `Delete`
and `Chmod` are kinds of `Write`. So `read_only()` forbids deletes and chmods
as well as writes.

## Effects

Import from `tools.filesystem.trusted.effects`. Every path argument must be
non-empty and have no lexical `..` component.

- `read_file(path) -> str`: read a UTF-8 text file.
- `read_lines(path) -> list[str]`: read UTF-8 logical lines without
  terminators, using Python `str.splitlines()`. Blank lines and a final
  unterminated line count; an empty file returns `[]`.
- `read_line(path, line_number) -> str`: read one one-based line without its
  terminator; empty at end of file. `line_number` must be positive.
- `grep(pattern, path) -> list[str]`: the lines of `path` containing the
  substring `pattern`.
- `find(path, args) -> list[str]`: list paths under `path` using `-name`,
  `-path`, `-type f/d/l`, `-mindepth` and `-maxdepth`, optionally negated with
  `-not`. Other arguments raise `ValueError` before `find` runs.
- `write_file(path, content)`: write UTF-8 text, creating parent directories.
- `append_file(path, text)`: append UTF-8 text, creating the file as needed.
- `make_dir(path)`, `touch(path)`: create a directory (with parents) or an
  empty file.
- `remove(path)`: delete a file, or a directory and everything under it.
- `copy(src, dest)`, `move(src, dest)`: copy or move a file or directory tree.
- `chmod(path, mode)`: set permission bits.

## Contracts

Import with `from tools.filesystem.trusted import contracts as fs`:

| Contract | Over |
| --- | --- |
| `fs.no_guarantees()` | nothing |
| `fs.no_reads()`, `fs.only_read_under(root)` | `Observe` (reads and listings) |
| `fs.never_read_under(prefix)`, `fs.reads_at_most(count)` | `Read` |
| `fs.lists_at_most(count)` | `List` |
| `fs.read_only()`, `fs.only_write_under(root)`, `fs.never_write_under(prefix)`, `fs.writes_at_most(count)` | `Write` (every modification) |
| `fs.must_not_write(substring)`, `fs.write_length_at_most(max_chars)` | `WriteText` |
| `fs.no_deletes()`, `fs.only_delete_under(root)`, `fs.never_delete_under(prefix)`, `fs.deletes_at_most(count)` | `Delete` |
| `fs.no_creates()`, `fs.creates_at_most(count)` | `Create` |
| `fs.no_chmods()`, `fs.only_modes(allowlist)` | `Chmod` |
| `fs.only_pattern(pattern)` | `grep` calls |

`only_*` and `never_*` are literal path-prefix policies over the symbolic call
argument: `only_read_under("test.md")` also admits `test.md.bak`. The effects
reject parent-directory segments first, so a path cannot satisfy a prefix and
then traverse outside it at runtime.

A path may name a directory, and `copy`, `move` and `remove` act on everything
under one. The `never_*` contracts therefore also refuse an event whose path is
a prefix of the forbidden one: `never_delete_under("/home/user/.ssh")` refuses
`remove("/home/user")`. Listing is not reading, so `never_read_under` allows
`find` over a directory containing the prefix. It reveals names, not contents;
use `only_read_under` to confine listings too.

## Example

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import copy, grep, read_file, write_file


@clauz3.guarantee(fs.only_read_under("/repo"))
@clauz3.guarantee(fs.only_write_under("/repo/build"))
@clauz3.guarantee(fs.never_read_under("/repo/.env"))
def main() -> None:
    source = read_file("/repo/src/app.py")
    print(grep("TODO", "/repo/src/app.py"))
    write_file("/repo/build/app.txt", source)
    copy("/repo/src/app.py", "/repo/build/app.py")
```

These guarantees confine the program to reading under `/repo` and modifying
under `/repo/build`, and keep it away from `/repo/.env` whichever tool it uses.

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
not add a phantom line; `hello\n\n` has two logical lines.

Sessions expose this as the `count-lines` recipe through `list_skills` and
`read_skill` when the installed filesystem library matches the bundled version.
After upgrading clauz3, an older copied filesystem library hides all recipes
that require it, including `read-file`, `write-and-read`, and directory-search
recipes, until that library is updated. The installed effects remain usable
through `tools()` and `run`; only the tested recipe previews are withheld.
Existing copied libraries need an operator-authorized update to gain new
effects; updating clauz3 alone does not rewrite project libraries. For an
unmodified bundled installation, the operator can run
`clauz3 install --force stdlib:filesystem` in the project and restart the session.
`--force` overwrites the installed filesystem library; preserve any local
customizations before choosing that update.

## Tests

```bash
just -f tests/Justfile test
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not.

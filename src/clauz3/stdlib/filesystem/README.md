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

This copies the library into `.clauz3/tools/filesystem/`. Then prove a program against
it:

```bash
clauz3 prove --trusted-root .clauz3/tools/filesystem/trusted plan.py
```

## Migrating from coreutils, editor, find, grep and text

These libraries were merged into `filesystem`, and an old copy defines effects
with the same names (`remove`, `append_file`, `find`, ...). Proving it beside
the new `filesystem` is refused as a duplicate effect, and installing the new
`filesystem` next to one is refused with the same advice. Remove the old
libraries (from the Libraries view, or delete them under `.clauz3/tools/` and,
for user-wide installs, `~/.local/share/clauz3/tools/`), then run
`clauz3 install --force stdlib:filesystem`.

Programs that imported them need the new names. `edit_file` is gone; use
`write_file`. Contracts not listed keep their names under `fs`:

| Old | New |
| --- | --- |
| `grep.searches_at_most` | `fs.reads_at_most` |
| `find.searches_at_most` | `fs.lists_at_most` |
| `find.never_read_under` | `fs.never_list_under` (`fs.never_read_under` covers contents only, not listings) |
| `editor.no_edits` | `fs.read_only` |
| `editor.only_edit_under`, `coreutils.only_create_under`, `only_copy_under`, `only_move_under`, `only_chmod_under` | `fs.only_write_under` |
| `editor.never_edit_under`, `coreutils.never_create_under` | `fs.never_write_under` |
| `editor.edits_at_most` | `fs.writes_at_most` |
| `editor.must_not_replace`, `must_not_append` | `fs.must_not_write` |
| `editor.replace_length_at_most`, `append_length_at_most` | `fs.write_length_at_most` |
| `coreutils.never_copy_from_under`, `never_move_from_under` | `fs.never_read_under` |

## Contracts cover events, not functions

Each effect declares, with `@performs`, the events it performs on the
filesystem (`events.py`):

| Effect | Events |
| --- | --- |
| `read_file(path)`, `read_lines(path)`, `read_line(path, line_number)` | `Read(path)` |
| `grep(pattern, path)` | `Read(path)` |
| `find(path, args)` | `List(path)` |
| `write_file(path, content)`, `append_file(path, text)` | `WriteText(path, text)` |
| `make_dir(path)` | `DirectoryCreate(path)` (a `Create`) |
| `touch(path)` | `Create(path)` |
| `remove(path)` | `Delete(path)` |
| `copy(src, dest)` | `RecursiveRead(src)`, `RecursiveWrite(dest)` |
| `move(src, dest)` | `RecursiveRead(src)`, `Delete(src)`, `RecursiveWrite(dest)` |
| `chmod(path, mode)` | `Chmod(path, mode)` |

The contracts are stated over events, so one guarantee covers every effect that
performs the event. `never_read_under("/repo/PRECIOUS.md")` refuses `read_file`,
`grep` and `read_line` on that file, and a `copy` or `move` from it.
Moving the file and reading it under a new name is refused at the move.

Events form a hierarchy, and a contract over an event covers its subclasses.
`RecursiveRead` is a kind of `Read`. `Read` and `List` are kinds of `Observe`.
`WriteText`, `Create`, `RecursiveWrite`, and `Chmod` are kinds of `Write`;
`Delete` is a `RecursiveWrite`, and `DirectoryCreate` is a `Create`. So
`read_only()` forbids deletes and chmods as well as writes. Each event still
counts once in its ancestor relation: a move counts as two modifications.

## Exact files or a directory

Use the same exact-file contract for one file or several:

```python
fs.only_reads_files([clauz3.FilePath("README.md")])
fs.only_reads_files([clauz3.FilePath("README.md"), clauz3.FilePath("LICENSE")])
fs.only_reads_files_under(clauz3.DirectoryPath("data"))
fs.only_writes_files([clauz3.FilePath("test.md")])
fs.only_writes_files_under(clauz3.DirectoryPath("data"))
```

Import `FilePath` and `DirectoryPath` from `clauz3`, or use the qualified names
above. Plain strings and the wrong path type are refused by these contracts.
`README.md` means that exact name, never `README.md.bak`. An empty file list
permits no corresponding reads or modifications. Directory `data` permits `data/a.txt` and subdirectories,
never `database/a.txt`; a trailing slash is optional. Both contracts accept
bare and leading `./` spellings of relative paths. `DirectoryPath(".")`
permits only relative paths; `DirectoryPath("/")` permits only absolute paths.

Exact-file read access forbids directory listings and all copy/move sources:
those tools can recursively read a directory, and a name alone cannot prove
it is a file. The directory contract allows these operations inside its
boundary. Read contracts do not restrict writes; combine with `read_only()` or
an appropriate write policy. Path types express lexical intent, without
checking existence or resolving symlinks or hard links. These contracts cover
filesystem-library events, not file access by unrelated libraries.

Exact-file write scope allows `write_file`, `append_file`, `touch`, and `chmod`
at the listed names. It refuses `copy`, `move`, and `remove` because their
arguments may be directories, and refuses explicit `make_dir`. Implicit parent
directories created by writing, appending, or touching a file are allowed;
these are not separate modification events. `only_writes_files_under` covers
all modification paths, including deletes and both ends of moves, within a
directory boundary. Its implicit parent-directory creation can extend above
that boundary. Write scopes do not restrict reads; add `no_reads()` when needed.

For a request such as “write hello to test.md”, use
`only_writes_files([FilePath("test.md")])`. The legacy
`only_write_under("test.md")` is a literal prefix and also allows
`test.md.bak`; it remains for compatibility. `write_file` can overwrite an
existing file. Neither typed scope asserts that a file is new or checks its
physical type, symlinks, or hard links.

## Effects

Import from `tools.filesystem.trusted.effects`. Every path argument must be
non-empty and have no `..` component, no `.` component after the first (`/./`,
a trailing `/.`), no empty component (`//`) and no backslash. A leading `./`
is allowed.

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
| `fs.only_reads_files(files: list[FilePath])` | exact file reads; no listings or copy/move |
| `fs.only_reads_files_under(directory: DirectoryPath)` | `Observe` within a directory boundary |
| `fs.only_writes_files(files: list[FilePath])` | exact modification paths; no recursive operations or explicit directory creation |
| `fs.only_writes_files_under(directory: DirectoryPath)` | `Write` within a directory boundary |
| `fs.no_reads()`, legacy `fs.only_read_under(root)` | `Observe` (reads and listings) |
| `fs.never_read_under(prefix)`, `fs.reads_at_most(count)` | `Read` |
| `fs.never_list_under(prefix)`, `fs.lists_at_most(count)` | `List` |
| `fs.read_only()`, legacy `fs.only_write_under(root)`, `fs.never_write_under(prefix)`, `fs.writes_at_most(count)` | `Write` (every modification) |
| `fs.must_not_write(substring)`, `fs.write_length_at_most(max_chars)` | `WriteText` |
| `fs.no_deletes()`, `fs.only_delete_under(root)`, `fs.never_delete_under(prefix)`, `fs.deletes_at_most(count)` | `Delete` |
| `fs.no_creates()`, `fs.creates_at_most(count)` | `Create` |
| `fs.no_chmods()`, `fs.only_modes(allowlist)` | `Chmod` |
| `fs.only_pattern(pattern)` | `grep` calls |

The older `only_read_under`, `only_write_under`, `only_delete_under`, and
`never_*` helpers are literal path-prefix policies over the symbolic call
argument: `only_read_under("test.md")` also admits `test.md.bak`. The effects
refuse `..`, `/./` and `//` first, so a path cannot satisfy a prefix and then
traverse outside it at runtime, or be respelled around a `never_*` prefix.
A prefix that no effect path can be written as, such as `data//secret`, or an
empty prefix, is refused instead of holding vacuously. The contracts also check
each event's path the way the effects do, so a tool from another layer that
performs these events cannot step around a prefix with `//`, `/./` or `..`.

The check is still lexical. A relative prefix is compared in both its bare and
`./` spellings, and `.` counts as an ancestor of every relative path, so
`never_delete_under("data")` refuses `remove(".")`. A `never_*` prefix refuses
any path written the other way, relative under an absolute prefix or absolute
under a relative one, because whether it reaches the prefix depends on the
working directory: `never_delete_under("/home/user/.ssh")` refuses
`remove(".")`. A path contains a prefix only at a `/` boundary, so
`never_write_under("/repo/notes.md.orig")` allows `/repo/notes.md`. Backslashes
are refused, since the library is POSIX. Symbolic links, hard links and
case-insensitive filesystems are not modelled.

`Delete` means `remove` or a `move`'s source. Overwriting a file with
`write_file`, `copy` or `move` is a `Write`, not a `Delete`, so protect contents
with `never_write_under`. Parent directories created on the way by
`write_file`, `touch` or `copy` are not `Create` events.

Every contract covers this library's tools only. `git`, `rsync` and `db` also
touch files, and their effects are not filesystem events, so `read_only()` says
nothing about a `git commit`. The approval text names "filesystem tools" for
that reason.

A path may name a directory, and `copy`, `move` and `remove` act on everything
under one. The `never_*` contracts therefore also refuse an event whose path is
a prefix of the forbidden one: `never_delete_under("/home/user/.ssh")` refuses
`remove("/home/user")`. Listing is not reading, so `never_read_under` allows
`find` over a directory containing the prefix. It reveals names, not contents;
use `never_list_under` to keep `find` away from a prefix, or `only_read_under`
to confine listings too.

## Example

```python
import clauz3
from tools.filesystem.trusted import contracts as fs
from tools.filesystem.trusted.effects import copy, grep, read_file, write_file


@clauz3.guarantee(fs.only_reads_files_under(clauz3.DirectoryPath("/repo")))
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

@clauz3.guarantee(fs.only_reads_files([clauz3.FilePath("./data/input.txt")]))
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

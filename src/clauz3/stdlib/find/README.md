# find

A trusted `clauz3` layer that lists files under a directory tree — a real
(non-mock) substitute for an agent's `find` / `fd` tool.

find **builds on `filesystem`**. Walking a tree reads the filesystem, so
`find.only_read_under` / `find.never_read_under` apply that layer's path rule
to the find effect itself. find adds its own `searches_at_most` contract on
top.

## Install

find depends on the `filesystem` layer, so install both:

```bash
clauz3 install stdlib:filesystem
clauz3 install stdlib:find
```

Prove a program against both trusted roots:

```bash
clauz3 prove \
  --trusted-roots tools/find/trusted tools/filesystem/trusted \
  plan.py
```

## Effect

Import with `from tools.find.trusted.effects import find`:

- `find(path: str, args: list[str]) -> list[str]` — list matching paths under
  one starting path. The path must be non-empty and contain no parent-directory
  segments. Arguments use this complete allowlist:

  | Filter | Accepted value |
  | --- | --- |
  | `-name` | Filename glob |
  | `-path` | Full-path glob |
  | `-type` | `f` (file), `d` (directory), or `l` (symlink) |
  | `-mindepth`, `-maxdepth` | Non-negative decimal integer |

Filters are combined with AND. `-not` can negate one `-name`, `-path`, or
`-type` filter. For example:

```python
find("/repo", ["-type", "f", "-not", "-path", "*/.git/*", "-maxdepth", "5"])
```

Every other option or action is rejected with `ValueError` **before starting
find**, including execution, deletion, output-file actions, boolean operators,
and changes to symlink traversal. Invalid options are checked by the trusted
implementation at runtime; a successful proof does not certify their validity.

Traversal always uses `-P`: symlinks encountered during the walk are not
followed. A bare symlink root such as `/repo/link` is also not followed, but
**root-path resolution can follow symlinks**: `/repo/link/` and `/repo/link/.`
resolve to the target directory before traversal, as can symlinks in ancestor
components. Contracts describe the lexical starting path; they do not provide
resolved-path confinement.

Relative roots other than `.` get a `./` prefix unless already prefixed, so option-like names
cannot change find's options. This prefix appears in returned paths and in
`-path` matching: for root `src`, use `-path ./src/*.py`, not `src/*.py`. Root
`./src` uses the same spelling without adding a second prefix. Root `.` likewise
returns `./src/app.py`, matching `-path ./src/*.py`. NUL-delimited
output preserves filenames containing newlines or carriage returns. Depth
options are emitted before ordinary filters to avoid GNU find's positional
option warning, regardless of their position in the argument list.

Existing installed libraries are project copies. After upgrading clauz3,
update an existing selection explicitly with `clauz3 install stdlib:find --force`
and restart the session. The formerly unrestricted find argument interface is
no longer supported.

## Contracts

Import with `from tools.find.trusted import contracts as find_rules`:

- `find_rules.no_guarantees()` — explicit null contract.
- `find_rules.only_read_under(root)` — every walk root is under `root`.
- `find_rules.never_read_under(prefix)` — no walk root is under `prefix`.
- `find_rules.searches_at_most(count)` — at most `count` walks occur.

`only_read_under` and `never_read_under` reuse the filesystem layer's policy
builders but constrain only find calls. State separate guarantees for
`read_file` or `grep` calls in the same program.

## Example

```python
import clauz3
from tools.find.trusted import contracts as find_rules
from tools.find.trusted.effects import find


@clauz3.guarantee(find_rules.only_read_under("/repo"))
@clauz3.guarantee(find_rules.searches_at_most(3))
def main() -> None:
    find("/repo/src", ["-name", "*.py"])
    find("/repo/tests", ["-name", "*.py"])
```

A walk of `/home/user/.ssh` would fail the `only_read_under("/repo")` proof.

## Tests

```bash
just -f tests/Justfile test
```

The find test Justfile points at both the find and filesystem layers, since
find imports filesystem.

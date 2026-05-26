# filesystem

A trusted `clauz3` layer for reading and writing files, with path-prefix
policies an agent can prove before any file is touched.

Unlike the layers under `examples/`, these effects are **real**, not mocks:
`read_file` actually reads and `write_file` actually writes. The prover never
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

Import with `from tools.filesystem.trusted.effects import read_file, write_file`:

- `read_file(path: str) -> str` — read a UTF-8 text file. Precondition: `path`
  is non-empty. Recorded under the `read` marker.
- `write_file(path: str, content: str) -> None` — write UTF-8 text, creating
  parent directories. Precondition: `path` is non-empty. Recorded under the
  `write` marker.

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
over the symbolic call argument.

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

## Tests

```bash
just -f tests/Justfile test
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not.

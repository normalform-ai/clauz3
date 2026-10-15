# coreutils

A trusted `clauz3` layer for the destructive bash-level file primitives —
`rm`, `mv`, `cp`, `mkdir`, `touch`, `chmod` — with path and mode policies an
agent can prove before anything is deleted, moved, or relaxed.

Where [`filesystem`](filesystem) reads and writes file *contents*, `coreutils`
covers the operations that **destroy or relocate** files. These are exactly the
shell one-liners an agent reaches for (`rm -rf`, `mv`, `cp`), and each has a
clean provable surface because its blast radius lives entirely in its path
arguments. (By contrast, wrappers like `docker run` or `pip install` can't be
meaningfully governed — their real effect is whatever code then executes, which
no contract over the invocation can see.)

The effects are **real**, not mocks, and use `pathlib`/`shutil`/`os` directly
(the same grain as `filesystem`), never a shell — so no argument is ever
shell-interpreted. The prover records each call as a fact and proves the
contract; the bodies run only under `clauz3 run`.

## Install

```bash
clauz3 install stdlib:coreutils
```

`make_dir` and `touch` have their own creation policy here
(`only_create_under` and friends), so this layer governs them without
depending on which others are installed:

## Effects

Import with `from tools.coreutils.trusted.effects import remove, move, copy, make_dir, touch, chmod`:

- `remove(path)` — delete a file, or a directory tree.
- `move(src, dest)` — move `src` to `dest` (fields `src`, `dest`).
- `copy(src, dest)` — copy a file or tree (fields `src`, `dest`).
- `make_dir(path)` — `mkdir -p`.
- `touch(path)` — create an empty file.
- `chmod(path, mode)` — set permission bits (fields `path`, `mode`).

All take non-empty paths as a precondition.

## Contracts

Import with `from tools.coreutils.trusted import contracts as cu`:

- `cu.no_guarantees()` — explicit null contract.
- **delete:** `cu.no_deletes()`, `cu.only_delete_under(root)`,
  `cu.never_delete_under(prefix)`, `cu.deletes_at_most(count)`.
- **move:** `cu.no_moves()`, `cu.only_move_under(root)` (dest),
  `cu.never_move_from_under(prefix)` (src).
- **copy:** `cu.no_copies()`, `cu.only_copy_under(root)` (dest),
  `cu.never_copy_from_under(prefix)` (src, an exfil guard).
- **chmod:** `cu.no_chmods()`, `cu.only_modes(allowlist)`,
  `cu.only_chmod_under(root)`.

`only_*` / `never_*` are `str.startswith` path-prefix policies.
`only_modes` is a whole-mode allowlist (bitwise masking isn't expressible in a
contract lambda), so e.g. `only_modes([0o644, 0o755, 0o600, 0o700])` admits no
world-writable, setuid, or setgid bits by construction.

`make_dir` / `touch` are covered by `only_create_under`, `never_create_under`,
`creates_at_most` and `no_creates`. They were once governed by
`filesystem.only_write_under`, because every write effect declared a `write`
marker and that contract matched the string; effects are addressed by name now,
so the policy that governs them lives here.

## Example

```python
import clauz3
from tools.coreutils.trusted import contracts as cu
from tools.coreutils.trusted.effects import remove, copy, chmod


@clauz3.guarantee(cu.never_delete_under("/etc"))
@clauz3.guarantee(cu.only_delete_under("/repo/build"))
@clauz3.guarantee(cu.never_copy_from_under("/home/user/.ssh"))
@clauz3.guarantee(cu.only_modes([0o644, 0o755]))
def main() -> None:
    remove("/repo/build/cache")
    copy("/repo/src/run.sh", "/repo/build/run.sh")
    chmod("/repo/build/run.sh", 0o755)
```

A `remove("/etc/passwd")`, a `copy` staging `/home/user/.ssh/id_rsa`, or a
`chmod(..., 0o777)` would each fail the proof.

## Tests

```bash
just -f tests/Justfile test
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not.

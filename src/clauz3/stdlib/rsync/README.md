# rsync

A trusted `clauz3` layer for synchronizing files with `rsync`, with
destination, source, and delete policies an agent can prove before any byte is
copied.

The effect is **real**, not a mock: `rsync` shells out to the `rsync` binary.
The prover never runs its body — it records each call as a fact and proves the
contract. The body runs only under `clauz3 run`, after a program is proved and
approved. `src`, `dest`, and the `delete` flag are first-class parameters
(rather than buried in an opaque flag list) precisely so the contracts can
statically prove *where* a sync may write and that it will not prune the
destination.

## Install

```bash
clauz3 install stdlib:rsync
```

This copies `tools/rsync/` into your project. Then prove a program against it:

```bash
clauz3 prove --trusted-root tools/rsync/trusted plan.py
```

## Effect

Import with `from tools.rsync.trusted.effects import rsync`:

- `rsync(src: str, dest: str, delete: bool, args: list[str]) -> str` — sync
  `src` to `dest` and return stdout. `delete` adds `--delete`. Precondition:
  `src` and `dest` non-empty. Arguments are passed as an explicit vector, so
  no field is interpreted by a shell.

## Contracts

Import with `from tools.rsync.trusted import contracts as rsync_rules`:

- `rsync_rules.no_guarantees()` — explicit null contract.
- `rsync_rules.no_syncs()` — the program performs no rsync calls.
- `rsync_rules.only_dest_under(root)` — every destination is under `root`
  (blast-radius guard).
- `rsync_rules.never_dest_under(prefix)` — no destination is under `prefix`.
- `rsync_rules.only_src_under(root)` — every source is under `root`.
- `rsync_rules.no_delete()` — no sync passes `--delete` (data-loss guard).
- `rsync_rules.syncs_at_most(count)` — at most `count` syncs occur.

`only_*` and `never_*` are path-prefix policies compiled to `str.startswith`;
`no_delete` constrains the boolean `delete` field.

## Example

```python
import clauz3
from tools.rsync.trusted import contracts as rsync_rules
from tools.rsync.trusted.effects import rsync


@clauz3.guarantee(rsync_rules.only_src_under("/repo"))
@clauz3.guarantee(rsync_rules.only_dest_under("/backup"))
@clauz3.guarantee(rsync_rules.no_delete())
def main() -> None:
    rsync("/repo/build/", "/backup/build/", False, ["-a"])
```

Stacking guarantees confines the sync to reading under `/repo`, writing under
`/backup`, and never pruning the destination. A sync into `/etc`, a source of
`/home/user/.ssh`, or a `delete=True` call would each fail the proof.

## Tests

```bash
just -f tests/Justfile test
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not.

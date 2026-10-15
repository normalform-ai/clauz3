# git

A trusted `clauz3` layer for running local `git` from a proved program, with
subcommand, location, and push-target policies an agent can prove before any
command runs.

These effects are **real**, not mocks: `git` shells out to the `git` binary and
`git_push` actually pushes. The prover never runs their bodies — it records each
call as a fact and proves the contract. The bodies run only under `clauz3 run`,
after a program is proved and approved. Arguments are passed as an explicit
vector (never a shell string), so a subcommand and its arguments cannot smuggle
extra commands.

## Install

```bash
clauz3 install stdlib:git
```

This copies `tools/git/` into your project. Then prove a program against it:

```bash
clauz3 prove --trusted-root tools/git/trusted plan.py
```

## Effects

Import with `from tools.git.trusted.effects import git, git_push`:

- `git(subcommand: str, args: list[str], cwd: str) -> str` — run
  `git <subcommand> <args...>` in `cwd` and return its stdout. Precondition:
  `subcommand` and `cwd` non-empty.
- `git_push(remote: str, branch: str, cwd: str) -> str` — push `branch` to
  `remote` from the repo at `cwd`. Precondition: all three non-empty.

## Contracts

Import with `from tools.git.trusted import contracts as git_rules`:

- `git_rules.no_guarantees()` — explicit null contract.
- `git_rules.no_git()` — the program runs no local git subcommands (does not by
  itself forbid `git_push`; stack `no_push()`).
- `git_rules.read_only()` — every subcommand is a read-only inspection command
  (`READ_ONLY_SUBCOMMANDS`: `status`, `log`, `diff`, `show`, `branch`, ...).
- `git_rules.only_subcommands(allowlist)` — every subcommand is in `allowlist`.
- `git_rules.never_subcommands(blocklist)` — no subcommand is in `blocklist`.
- `git_rules.only_in(root)` — every subcommand runs in a directory under `root`.
- `git_rules.runs_at_most(count)` — at most `count` subcommands run.
- `git_rules.no_push()` — the program performs no pushes.
- `git_rules.only_push_to_remote(remote)` — every push targets `remote`.
- `git_rules.only_push_to_branch(branch)` — every push targets `branch`.

`read_only`, `only_subcommands`, and `never_subcommands` are subcommand-name
policies; `only_in` is a path-prefix policy compiled to `str.startswith`.

## Example

```python
import clauz3
from tools.git.trusted import contracts as git_rules
from tools.git.trusted.effects import git, git_push


@clauz3.guarantee(git_rules.never_subcommands(["reset", "clean"]))
@clauz3.guarantee(git_rules.only_in("/repo"))
@clauz3.guarantee(git_rules.only_push_to_branch("main"))
def main() -> None:
    git("add", ["-A"], "/repo")
    git("commit", ["-m", "automated update"], "/repo")
    git_push("origin", "main", "/repo")
```

Stacking guarantees confines the program to a single working tree, forbids
destructive subcommands, and pins the push target to `main`. A `git("clean", ...)`
or a `git_push("origin", "release", ...)` would fail the proof.

## Tests

```bash
just -f tests/Justfile test
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not.

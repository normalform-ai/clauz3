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
  `git <subcommand> <args...>` in `cwd` and return its stdout. Preconditions:
  `subcommand` and `cwd` non-empty; `subcommand` is an allowed built-in git
  command (`is_builtin_subcommand`). That leaves out `push` and every other
  command that pushes or publishes (`send-pack`, `send-email`, `imap-send`,
  ...), so pushes always go through `git_push`; commands built to run other
  programs or git commands (`for-each-repo`, `filter-branch`, `difftool`,
  `mergetool`, `merge-index`, `hook`, `maintenance`); and synonyms (`stage`,
  `annotate`, `pickaxe`, `init-db`, `fsck-objects`). Aliases and third-party
  `git-*` commands are refused.
- `git_push(remote: str, branch: str, cwd: str) -> str` — push `branch` to
  `remote` from the repo at `cwd`. Preconditions: all three non-empty;
  `remote` and `branch` do not start with `-`.

Every git command runs with `-c core.hooksPath=/dev/null`, so repository hooks
never run, even ones the program writes into `.git/hooks`.

Preconditions are checked by the prover and again by the effect bodies when
the program runs. One check happens **only when the program runs**: an
argument starting with `--output` (which makes `diff`/`log`/`show` write a
file) raises `ValueError`. The prover cannot see it, so a program refused this
way may already have run its earlier calls.

## Contracts

Import with `from tools.git.trusted import contracts as git_rules`:

- `git_rules.no_guarantees()` — explicit null contract.
- `git_rules.no_git()` — the program runs no local git subcommands (does not by
  itself forbid `git_push`; stack `no_push()`).
- `git_rules.read_only()` — every subcommand only looks at the repository
  (`READ_ONLY_SUBCOMMANDS`: `status`, `log`, `diff`, `show`, `rev-parse`, ...)
  and nothing is pushed.
- `git_rules.only_subcommands(allowlist)` — every subcommand is in `allowlist`.
- `git_rules.never_subcommands(blocklist)` — no subcommand is in `blocklist`.
- `git_rules.only_in(root)` — every subcommand and push runs in a directory
  whose path starts with `root`.
- `git_rules.runs_at_most(count)` — at most `count` subcommands run.
- `git_rules.no_push()` — the program performs no pushes. Commands that can be
  told to run other programs (`rebase --exec`, `submodule foreach`,
  `bisect run`, `fetch --upload-pack`, `grep -O`, settings such as
  `core.fsmonitor`) are not covered; `read_only()` excludes them.
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

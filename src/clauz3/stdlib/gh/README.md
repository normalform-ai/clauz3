# gh

A trusted `clauz3` layer for running the GitHub CLI (`gh`) from a proved
program, with verb, object, and repository policies an agent can prove before
any command runs.

The effect is **real**, not a mock: `gh` shells out to the `gh` binary. The
prover never runs its body — it records each call as a fact and proves the
contract. The body runs only under `clauz3 run`, after a program is proved and
approved. Arguments are passed as an explicit vector (never a shell string), so
no field is interpreted by a shell.

Every invocation has the shape `gh <command> <action> --repo <repo> <args...>`.
Recording `command` (the GitHub object, e.g. `pr`) and `action` (the verb, e.g.
`view`) separately is the policy surface a raw shell allowlist cannot express:
`read_only` constrains the verb while `only_commands` and `only_repo`
independently bound the object and the repository.

## Install

```bash
clauz3 install stdlib:gh
```

This copies `tools/gh/` into your project. Then prove a program against it:

```bash
clauz3 prove --trusted-root tools/gh/trusted plan.py
```

## Effects

Import with `from tools.gh.trusted.effects import gh`:

- `gh(command: str, action: str, args: list[str], repo: str) -> str` — run
  `gh <command> <action> --repo <repo> <args...>` and return its stdout.
  `--repo` comes first, so a trailing value-taking flag cannot swallow it, and
  `repo` must be an `owner/name` slug (gh reads `HOST/OWNER/REPO` as a host).
  Preconditions: `command`, `action`, and `repo` non-empty; `command` is one
  of gh's built-in commands (`is_builtin_command`), so aliases and extensions
  are refused. `gh api` is not available: it ignores `--repo` for a literal
  endpoint and accepts any method, so it could reach any repository or
  account. `action` must be a word, not a flag or link (`is_plain_action`); action aliases are refused (`is_canonical_action`: write
  `list`, not `ls`; `delete`, not `remove`); `issue transfer` and `label clone`
  are refused because they reach a second repository; and `args` may not
  contain flags that widen the command past `--repo` (`has_no_scope_flags`:
  `--org`/`-o`, `--user`/`-u`, `--owner` and its variants, `--repos`,
  `--hostname`, `--source`, `--branch-repo`, an agent-supplied `--repo`/`-R`,
  or a bare `--`). `-o` and `-u` are also short forms of a few harmless flags
  (`repo read-file --output`, `repo clone --upstream-remote-name`,
  `repo set-default --unset`); use their long names.

Preconditions are checked by the prover and again by the effect body when the
program runs. These checks happen **only when the program runs** (the prover
cannot see them, so a program refused this way may already have run its
earlier calls):

- Web addresses: refer to pull requests and issues by number, not URL. gh
  resolves a URL against its own repository, ignoring `--repo`, so any
  argument containing `://` or `github.com` raises `ValueError`, as does a
  scheme-less address such as `ghe.example.com/owner/repo/pull/5` or
  `owner/repo/issues/7` (gh would add `https://` and use its repository). Put text
  containing links in a file and pass `--body-file`.
- `--flag=value`: pass long flags as separate arguments (`["--body", "text"]`),
  so each flag has one spelling a contract can check. gh accepts no flag
  abbreviations, so `--adm` is not `--admin`.
- Short-option groups containing `o`, `u` or `R`, such as `-Rother/repo`, or
  `S` grouped with other letters (`-wS`).

## Contracts

Import with `from tools.gh.trusted import contracts as gh_rules`:

- `gh_rules.no_guarantees()` — explicit null contract.
- `gh_rules.no_gh()` — the program makes no GitHub-CLI calls.
- `gh_rules.read_only()` — every invocation uses a read-only verb
  (`READ_ONLY_ACTIONS`: `view`, `list`, `status`, `diff`, `checks`).
- `gh_rules.only_commands(allowlist)` — every command (object) is in
  `allowlist`, e.g. `only_commands(["pr", "issue"])`.
- `gh_rules.never_actions(blocklist)` — no action (verb) is in `blocklist`,
  e.g. `never_actions(["merge", "close", "delete"])`.
- `gh_rules.only_repo(repo)` — every invocation targets the `owner/name` slug
  `repo` with a command that works on a single repository
  (`REPOSITORY_COMMANDS`: browse, discussion, issue, pr, release, repo, cache,
  run, workflow, label, ruleset, secret, variable), and none uses a
  `--search`/`-S` filter. Account-level commands such as `gist` or `ssh-key`,
  and `gh search`, fail the proof.
- `gh_rules.only_owner(owner)` — the same, for any `owner/<name>` slug.
- `gh_rules.runs_at_most(count)` — at most `count` invocations occur.
- `gh_rules.no_admin_override()` — no invocation passes `--admin`, which
  overrides branch protection and required reviews (e.g. `gh pr merge 42
  --admin`). State this guarantee unless the user explicitly asked for an
  admin override.

`read_only`, `only_commands`, and `never_actions` are name-set policies;
`only_repo` is an equality policy and `only_owner` is a `str.startswith` prefix
policy. `only_repo` and `only_owner` check the `--repo` value; web-address
arguments that would override it are refused by the effect, as are
`issue transfer` and `label clone`, which name a second repository as an
ordinary argument. Search is forbidden under both because a query qualifier
such as `repo:other/name` reaches other repositories.

## Example

```python
import clauz3
from tools.gh.trusted import contracts as gh_rules
from tools.gh.trusted.effects import gh


@clauz3.guarantee(gh_rules.only_repo("cmungall/agent-deal"))
@clauz3.guarantee(gh_rules.only_commands(["pr", "issue"]))
@clauz3.guarantee(gh_rules.never_actions(["merge", "close", "delete"]))
@clauz3.guarantee(gh_rules.no_admin_override())
def main() -> None:
    gh("pr", "view", ["42"], "cmungall/agent-deal")
    gh("pr", "comment", ["42", "--body", "looks good"], "cmungall/agent-deal")
```

Stacking guarantees confines the program to one repository, to pull requests
and issues, and forbids the destructive verbs. A `gh("pr", "merge", ...)` or a
`gh("repo", "delete", ...)` would fail the proof, as would any call against a
different repository.

## Tests

```bash
just -f tests/Justfile test
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not.

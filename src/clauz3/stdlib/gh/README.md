# gh

A trusted `clauz3` layer for running the GitHub CLI (`gh`) from a proved
program, with verb, object, and repository policies an agent can prove before
any command runs.

The effect is **real**, not a mock: `gh` shells out to the `gh` binary. The
prover never runs its body — it records each call as a fact and proves the
contract. The body runs only under `clauz3 run`, after a program is proved and
approved. Arguments are passed as an explicit vector (never a shell string), so
no field is interpreted by a shell.

Every invocation has the shape `gh <command> <action> <args...> --repo <repo>`.
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
  `gh <command> <action> <args...> --repo <repo>` and return its stdout.
  Precondition: `command`, `action`, and `repo` non-empty.

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
  `repo`.
- `gh_rules.only_owner(owner)` — every targeted repository belongs to `owner`
  (matches any `owner/<name>` slug).
- `gh_rules.runs_at_most(count)` — at most `count` invocations occur.

`read_only`, `only_commands`, and `never_actions` are name-set policies;
`only_repo` is an equality policy and `only_owner` is a `str.startswith` prefix
policy.

## Example

```python
import clauz3
from tools.gh.trusted import contracts as gh_rules
from tools.gh.trusted.effects import gh


@clauz3.guarantee(gh_rules.only_repo("cmungall/agent-deal"))
@clauz3.guarantee(gh_rules.only_commands(["pr", "issue"]))
@clauz3.guarantee(gh_rules.never_actions(["merge", "close", "delete"]))
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

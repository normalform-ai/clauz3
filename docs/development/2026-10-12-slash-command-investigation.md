# Restricted Claude sessions and native commands

Exploratory checks with the installed Claude Code 2.1.283, followed by the
implementation and validation below. These are targeted compatibility checks,
not a complete security review or guarantee for other Claude versions.

## Findings

The original launcher passed `--disable-slash-commands`, and
`test_command_has_only_session_tools` required it. In an interactive probe,
typing `/status` produced no completion and submitting it returned
`Unknown command: /status`.

Removing that flag while retaining the existing tool, settings, hook and MCP
restrictions restored native diagnostics. The candidate additionally set:

```json
{
  "disableBundledSkills": true,
  "disableSkillShellExecution": true
}
```

The first setting is documented; the second was found in the installed CLI's
settings schema and execution checks. Its shell-blocking behavior was not
exercised in this investigation. Do not depend on it as the sole boundary.

| Probe | Observed result |
| --- | --- |
| Existing blanket flag | `/status` unknown; SDK initialization advertises no commands |
| Candidate, `/status` | Opens version/model/configuration status |
| Candidate, `/model` | Opens model picker |
| Candidate, `/skills` | Reports no skills despite planted user/project skill fixtures |
| Candidate, `/mcp`, no servers | Reports no configured servers |
| Candidate, explicit local MCP fixture | `/mcp` reports one connected server |
| Candidate plus `--restricted` | Explicit MCP fixture still connects; built-in command inventory remains broad |
| Candidate plus `--safe-mode` | Explicit MCP fixture disappears; unsuitable for the session bridge |
| Candidate with `--setting-sources user,project` | User/project skill and legacy `.claude/commands` fixtures appear in SDK initialization |

`--setting-sources ""` is doing more useful work than its name suggests: the
installed skill discovery code checks whether user/project settings sources
are enabled before loading their skill directories. The positive control
confirmed that the test fixtures were valid and discoverable when enabled.
The same comparison excluded both legacy command fixtures with empty sources.

The SDK command inventory is the non-interactive inventory, not the complete
terminal menu: `/status` and `/skills` were checked separately in the terminal.
With the candidate, the SDK still advertised commands including `/doctor`,
`/init`, `/security-review`, `/reload-skills`, `/reload-plugins`, `/heapdump`,
and workflow-related commands. Advertisement alone does not establish that a
command executes successfully or bypasses restrictions.

## What the narrower controls do not solve

- There is no documented general built-in command allowlist in the CLI
  reference. The installed CLI has a session skill allowlist populated through
  SDK initialization; this is not an interactive CLI command allowlist.
- `disableBundledSkills` intentionally leaves some built-in prompt commands
  typable. `/doctor` is a documented exception. Disabling the Skill tool alone
  does not establish a boundary for human-typed native commands.
- `skillOverrides` is not a general built-in command filter. The inspected
  implementation handles prompt skills and a special case for
  `auto-mode-setup`; ordinary local commands are not generally filtered by it.
- `/status` opens a tabbed interface that also contains configuration. It is
  not an isolated read-only status page.
- `/model` offers both a persistent default and a session-only selection.
- Native `/skills` does not list the documentation served by clauz3's
  `list_skills` and `read_skill`. Claude session status is also distinct from
  the clauz3 `status` tool's proof/approval/run status.
- `--restricted` is useful additional protection (including refusal of
  bypass-permissions mode and cloud-session creation), not a command allowlist
  or a replacement for `--tools ""` and strict MCP configuration.

## Threat model and possible direction

The documented clauz3 boundary already trusts the local user, the harness and
administrator configuration. Under that model, exposing human-operated native
controls can be reasonable, provided agent-originated effects remain confined
to the bridge. It would be inaccurate to describe this as allowing only
read-only commands or enforcing every human action through clauz3 approval.

A candidate implementation could replace the blanket flag with bundled-skill
disablement, retain all current capability restrictions, and evaluate
`--restricted` as additional protection. Selected documentation should continue
through `list_skills`/`read_skill`, without native skill activation.

Before making that the default, test authenticated account-synced and plugin
skills, managed configuration, skill/command name collisions, reloads, native
prompt commands, permission-mode changes, and child/resumed sessions. Verify
the actual model tool inventory remains restricted after these operations.
Pin or gate any newly required CLI features; these probes cover one version.

If the requirement is literally a small command allowlist, it needs upstream
support or a clauz3-owned interface. A growing blacklist of native commands is
not equivalent.

## Probe isolation and sources

The probes used a temporary `CLAUDE_CONFIG_DIR` and project, a dummy API key,
and `ANTHROPIC_BASE_URL=http://127.0.0.1:9`. Nonessential traffic and updates
were disabled. No successful model request, real approval, or installed tool
effect was involved. MCP compatibility used a synthetic stdio server with one
read-only tool, not a full clauz3 launcher/approval lifecycle.

## Implementation validation

The launcher now removes the blanket command ban, adds `--restricted` in the
default mode, and supplies the two candidate settings above. It also disables
account skill/plugin syncing for the invocation and explicitly sets the
`doctor` skill override to `off`. The debug `--unrestricted` mode retains native
tools and normal permission prompts. Start and resume require Claude Code
2.1.283 or newer; invalid, failed, timed-out, and older version probes refuse
startup before the approval service starts.

A follow-up smoke check used `_claude_command` from the modified launcher and
the actual `clauz3.sessions.tools` bridge. Authentication remained a dummy key;
model requests went to a temporary localhost server returning a fixed text
response, with no real model or tool effect:

- `/mcp` reported one connected server.
- `/reload-skills` reported zero skills, with the same planted user/project
  skill and legacy-command fixtures present.
- A normal prompt completed, and the captured model request offered exactly
  `mcp__clauz3__run`, `tools`, `libraries`, `status`, `list_skills`, and
  `read_skill` (all under the `mcp__clauz3__` prefix). There were no native tools.

A second check used one SDK session: reload skills, request MCP reconnect,
then submit a normal prompt. Reload again found zero skills. The strict
configuration refused MCP reconnect/enable/disable, while inspection remained
available. The subsequent model request still offered exactly the six clauz3
tools. SDK initialization advertised `/mcp` and `/model`, but neither `/doctor`
nor any planted skill/legacy command.

Interactive checks of the final generated command opened `/status` (showing
the connected bridge) and `/model`. A local-response probe of `/init` still
offered only the six clauz3 tools. `/doctor` and its `/checkup` alias both
returned the explicit disabled-skill message without making a model request.

These checks do not establish tool-inventory stability across every
interactive configuration change. Authenticated account syncing and
administrator policy remain outside this local fixture's coverage.

Automated validation: all 57 tests in `tests/test_start.py` and
`tests/test_session_skills.py` passed, including the approval lifecycle and
resume tests. `just check` and `just docs` passed. A broader `just test` run was
stopped around 49% after its long interpreter corpus sweep passed; it reported
no failures before termination, but this is not a complete `just test` pass.

## Sources

- [Claude CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Commands](https://code.claude.com/docs/en/commands)
- [Skills](https://code.claude.com/docs/en/skills)
- [Environment variables](https://code.claude.com/docs/en/env-vars)
- Installed `claude --help` and local CLI implementation, version 2.1.283

# CLI reference

The `clauz3` command is the single entry point for the prover, the approval
service, the runner, and the trusted-layer installer. This page lists the
subcommands and their flags. Run `clauz3 <subcommand> --help` for the
authoritative output from your installed version.

## `clauz3`

```
clauz3 [--version] {ui,start,resume,prove,run,tools,libraries,mock-approval-service,approval-service,approval-keygen,policy-check,install,test,lint,config} ...
```

Static contract proofs for agent-authored Python.

## `clauz3 ui`

Open chat, proof progress, and approvals together in a local browser workspace:

```bash
# Requires Node.js 22+ and an authenticated Claude installation.
npm install -g @agentclientprotocol/claude-agent-acp@0.89.1
clauz3 ui claude
# `clauz3 ui` also defaults to Claude.
```

Other coding tools are available in both frontends:

```bash
clauz3 ui codex --unrestricted
clauz3 start codex --unrestricted
clauz3 ui agy --unrestricted
clauz3 start opencode --unrestricted --model PROVIDER/MODEL
clauz3 ui pi
clauz3 start pi
```

| Agent | Optional runtime | Authentication |
| --- | --- | --- |
| `claude` | UI: `npm install -g @agentclientprotocol/claude-agent-acp@0.89.1`; terminal: Claude Code | Sign in with Claude Code. |
| `codex` | `npm install -g @agentclientprotocol/codex-acp@2.2.2` | Existing Codex/ChatGPT login, or the adapter's supported API-key environment. |
| `agy` | [Antigravity CLI](https://antigravity.google/download) | Sign in with `agy`; permit the Clauz3 MCP tools as described below. |
| `opencode` | `npm install -g opencode-ai` | `opencode auth login`, or a configured provider key; select a usable `--model provider/model`. |
| `pi` | `npm install -g @earendil-works/pi-coding-agent@0.82.1` | Sign in using Pi's `/login`; OAuth and literal API keys are reused. |

Only install the runtime you use. These are optional external executables;
Clauz3 does **not** add provider SDKs to its Python dependencies or install npm
packages automatically. Codex's ACP package carries its compatible SDK/runtime.
Restricted Pi uses the installed runtime's supported SDK and a packaged Clauz3
MCP client; unrestricted Pi uses RPC and a Clauz3 extension. Antigravity uses
stream-json. No separate Pi or Antigravity ACP wrapper is required.

Codex, Antigravity, and OpenCode currently require `--unrestricted`. Their native tools,
loaded user configuration, and extensions have **not** been verified as a
Clauz3 confinement boundary. Native effects may bypass proofs and approval.
Clauz3 `run` still requires its proof and signed approval. No provider receives
the approval signing key or decision credential. Unexpected native ACP
permission requests are denied, not automatically approved; Codex's correlated
requests for the six Clauz3 MCP tools receive single-call permission only.

**Restricted Pi:** the reviewed SDK version is pinned, and startup rejects any
other version. Its exact tool allowlist contains only the six Clauz3 bridge
tools; native read, shell, edit, write, and additional tools are not executable.
An empty resource loader disables global/project extensions, packages, context
files, prompt templates, and native skills. Clauz3 supplies its selected skill
documents through the bridge. Settings are constructed in memory, copying only
`defaultProvider` and `defaultModel` from Pi's global settings; `--model
provider/model` overrides these. Custom model definitions and remote model
catalog loading are disabled. OAuth credentials remain in Pi's existing auth
file; credential shell commands are refused. Node preload hooks and provider
endpoint overrides are not inherited. Sessions resume with the same restriction;
changing between restricted and unrestricted Pi requires a new conversation.
This confines agent tools, rather than sandboxing the trusted Pi SDK process.

**Codex:** the official TypeScript SDK wraps the CLI and does not expose the
exact tool allowlist required here. Sandbox and approval controls alone do not
establish this boundary. Clauz3 does not build or maintain a patched Codex
runtime; see the [integration investigation](../todos/codex-launcher.md).
For OpenAI models with restricted tools today, Pi also supports a Codex/ChatGPT
provider: sign in through Pi's `/login`, then use, for example,
`clauz3 ui pi --model openai-codex/gpt-5.4`. This uses Pi as the agent harness
and Pi's own saved OAuth credentials, rather than the Codex CLI session.

The browser's **Agent for new conversations** selector changes the provider
when you press **New conversation**. Saved chats retain their own provider and
model. The UI identifies unrestricted mode throughout the session. A restricted
server cannot resume an unrestricted chat; its transcript remains readable.
Use `--model` at launch to override the selected provider's model. A provider
switch starts with that provider's default model instead of carrying a model
name from another provider.

Claude's browser integration is always restricted: `clauz3 ui claude
--unrestricted` is rejected, and Claude is disabled in the provider selector
of an unrestricted workspace. Start a separate `clauz3 ui claude` workspace
for restricted Claude, or use `clauz3 start claude --unrestricted` for native
Claude terminal tools.

Antigravity's headless permissions need these entries in `permissions.allow`
in `~/.gemini/antigravity-cli/settings.json` (merge with existing settings):

```json
{
  "permissions": {
    "allow": [
      "mcp(clauz3/run)", "mcp(clauz3/status)", "mcp(clauz3/tools)",
      "mcp(clauz3/libraries)", "mcp(clauz3/list_skills)", "mcp(clauz3/read_skill)"
    ]
  }
}
```

Clauz3 does not edit this user-wide file. Denials are reported with setup
instructions. Antigravity has no invocation-local MCP flag, so its subprocess
runs in a private temporary workspace containing the bridge configuration;
the bridge executes against the original project and selected libraries.
Use Clauz3 tools for project access. Existing OAuth credentials remain in their
normal location. Temporary bridge configuration is removed on normal exit.

Run from the project directory. The command uses the same selected skill
snapshots, approval policies, and remembered approval profile as `clauz3 start`.
Library setup happens in the browser; an empty folder or `--setup` opens
**Libraries** directly. Supported flags are `--setup`, `--skills-dir` (repeatable),
`--policy`, `--model`, `--prompt`, `--port`, `--unrestricted`, and `--no-browser`. The last flag
prints the private login URL instead of opening it. The server binds only to
`127.0.0.1`. Press Ctrl-C in the terminal to stop it.

The workspace shows streaming answers, tool input/output, plans, and action
cards with actual runner phases. Each approval card includes guarantees,
coverage, source, proofs, and approve/reject controls. Remembered decisions
use the existing independently verified approval profile. Follow the sidebar
link to manage that profile; returning to the conversation restores it.

**Libraries** lists bundled, curated, and configured global sources. Review
files, implementations, contracts, and setup requirements before confirming an
install, update, or removal. Save a selection and start a new conversation to
use it; required library dependencies are included automatically. Changes never
alter a running conversation's selection. Stop a conversation before updating
or removing libraries it uses. Previous copies are kept under
`.clauz3/library-backups/`. External runtime dependencies and authentication
still need the setup shown in the review.

One agent conversation and one connected browser tab are active at a time.
Reloads preserve the running agent. **New conversation** saves the previous
chat, and the sidebar lets you search, rename, and reopen earlier conversations.
Selecting a chat resumes its saved agent context and library selection with
fresh submission credentials. If those library files have changed, its transcript
remains available; start a new conversation to use the changed tools. **Stop**
terminates the agent and workers; completed effects are not undone. Wait or stop before switching an active task.

Chat history survives server restarts in `conversations.sqlite` under the
user-wide Clauz3 store (`CLAUZ3_HOME`, otherwise `$XDG_DATA_HOME/clauz3` or
`~/.local/share/clauz3`). Unsent drafts stay in the browser tab, separately for
each conversation. The service reopens the most recently updated chat at startup.
The approval profile and decision history use the matching workspace design,
including the standalone service's `/ui/profile` page. CLI `clauz3 resume`
continues to use its separate managed-session records.

The Claude adapter is pinned to the reviewed `0.89.1` release. Unknown releases
are refused before starting the service. This adapter check is separate from
the Claude CLI version requirement for `clauz3 start` below; the workspace does
not expose the terminal's native operator commands. The host supplies an empty native
tool set, only the six Clauz3 MCP tools, strict MCP configuration, empty settings
sources, and disabled hooks, plugins, connectors, and slash commands. Unexpected
ACP permission requests are refused: they cannot replace signed Clauz3 approval.
The browser cannot select an executable, working directory, additional MCP
servers, or SDK options. In restricted Claude mode, this is harness confinement rather
than an OS sandbox against other processes running as the same user.

Text prompts are supported. Attachments, agent slash commands, model switching
during a conversation, concurrent agent conversations, and remote hosting are outside
this first version. See [workspace architecture](../development/workspace-ui.md)
for the implementation and verification commands.

## `clauz3 start`

Start an approval service and an agent session in the current folder:

```bash
clauz3 start claude
```

Requires an installed, authenticated Claude Code CLI, version **2.1.283 or
newer**. The launcher checks the version before starting its services because
older clients may not enforce the native-command restrictions. In an empty
project, the launcher offers a library chooser before starting the agent. Press Enter
to install the recommended File work bundle (`filesystem`, `find`, `grep`,
`editor`), choose File work + Git/GitHub, select individual bundled or global
libraries, or continue without tools. Selection is made by the user; the agent
cannot install libraries. Existing project libraries are preserved.

Without a terminal, install libraries first with `clauz3 install`; there is no
implicit installation or prompt on piped input. macOS and Linux are supported.
Claude uses its native terminal interface. Other providers use a line-oriented
Clauz3 terminal client over the same conversation host as the UI: type a prompt,
wait for the response, and use `/exit`, EOF, or Ctrl-C to stop. Piped prompts are
processed sequentially. The printed `clauz3 resume ID` command restores the
provider and conversation with fresh bridge credentials.

| Option | Description |
| --- | --- |
| `--no-browser` | Print the private approver URL without opening a browser. |
| `--port PORT` | Bind localhost on this port; default `0` chooses a free port. |
| `--policy PATH` | Use an operator-authored approval policy, with the installed roots authorized automatically. |
| `--model NAME` | Select the agent model (provider-specific name). |
| `--prompt TEXT` | Start the interactive session with this prompt. |
| `--skills-dir DIR` | Select inert skill documents from a folder; repeat for additional folders. |
| `--setup` | Reopen the library chooser for a project that already has tools. |
| `--unrestricted` | Enable native tools that can bypass Clauz3. Required for Codex, Antigravity, and OpenCode; optional debug mode for Claude and Pi. |

The launcher creates fresh signing keys and separate submission and approver
tokens. The agent receives only the submission token and public verifying key;
the signing key stays in the service process and the approver URL goes to the
operator's terminal/browser. No environment exports or project configuration
edits are needed. The service must become ready before the agent starts; it
stops on agent exit, Ctrl-C, or SIGTERM. A service failure stops the agent.
Agent exit codes are preserved (`130` for interruption, `143` for SIGTERM).

Before launching an agent, it checks that the MCP bridge can initialize and
expose its selected session tools using the same Python installation. A broken
bridge stops startup with the underlying error. The MCP SDK dependency is
restricted to the supported 1.x series, including fresh `uvx` installations.

The default tool surface contains `mcp__clauz3__run`, which invokes
`clauz3 run` with Python source on stdin; `mcp__clauz3__tools`, which searches
the installed public API (use `view="source"` for implementation detail);
`mcp__clauz3__libraries`, the read-only
`clauz3 libraries` catalog; and `mcp__clauz3__status`, which reports active and
recent runs. `mcp__clauz3__list_skills` and `mcp__clauz3__read_skill` are always
available for built-in documentation and any operator-selected documents.
The agent can discover uninstalled libraries and
suggest install commands to the user. After installation, restart the session
to include newly selected roots. A session started without libraries can browse
the catalog, but refuses program execution. The agent cannot pass shell commands,
change runner flags or roots, or install tools through these interfaces.

Before submitting its first program, the agent is instructed to read the built-in
`builtin:clauz3-authoring` document. It explains imports, literal guarantee
arguments, supported control flow, loop limits, and proof/approval status. Tested
recipes appear in `list_skills()` when all required project library Python files
match the bundled versions. Edited or older copies do not get recipes advertised
as compatible. The recipes cover reading, writing then reading back, grepping
named files, listing file names, and searching a directory of UTF-8 files using
symbolic string-list iteration. The directory recipe reports skipped paths;
binary or unreadable files can still abort it. A fixed-length loop is not a
complete traversal of an unknown-size directory. Generated library skill
files point to this guide rather than shipping an ellipsis-only program.

Session proofs have a **30-second total wall-clock budget**, including worker
startup, source checking, and symbolic construction. The operator can change it
with `CLAUZ3_PROOF_TIMEOUT_SECONDS=60 clauz3 start claude` (finite and positive).
The agent cannot override it in a tool call. Individual solver checks also keep
their existing limits, clamped to the remaining budget; an unresolved obligation
stops an interactive proof immediately. Timeout/unknown is a refusal, never
permission to execute. Standalone `clauz3 prove`/`run` behavior is unchanged.

Use `status()` when a harness backgrounds a call. It reports `checking`,
`proving`, `submitting_approval`, `awaiting_approval`, `executing`, and terminal
`completed`/`refused`/`failed`/`cancelled` states. `awaiting_approval` is reported
only after the service confirms the request is pending. Approval waits use the
separate approval timeout; they do not consume the proof budget. Proof details
include the current obligation and, where available, its source line, call, and
predicate. `source_kind: submitted` positions refer to the original program,
including when required policies rewrite the proved copy. Injected obligations
are marked `required_policy` without invented source positions. Library or
unmapped generated expressions are identified separately without claiming
positions in the submitted program. MCP progress notifications expose the same information to supporting
clients. Status keeps at most 20 runs in memory for this bridge lifetime.
Only one run may be active; cancelling it terminates its owned worker group.

Native agent tools, user/project skills and custom commands, bundled skills,
hooks, and user/project settings are disabled for the session. Account skill
and plugin syncing is disabled for this invocation, and skill shell
preprocessing is disabled. `/doctor`, which otherwise survives bundled-skill
disablement, is explicitly disabled too. Only the session MCP configuration
is loaded. Restricted sessions also use Claude's `--restricted` mode to refuse
bypass-permissions mode and cloud-session creation. This uses Claude's
[CLI restriction flags](https://code.claude.com/docs/en/cli-reference), so a
current Claude Code installation is required. Authentication remains the
CLI's responsibility; custom authentication settings in ignored settings
files may need their environment-variable equivalents.

These are harness restrictions, not an OS sandbox. The harness, its managed
administrator configuration, the installed tool layer, and the local user
remain trusted. The standalone `config` command is a separate, more permissive
workflow; `start` neither requires nor rewrites its settings.

Native operator commands remain available, including `/status`, `/model`,
`/mcp`, `/help`, and `/context`. This is not a read-only command allowlist:
Claude's built-in configuration and session controls remain operator actions
outside clauz3's proof/approval workflow. Changing the model does not change
clauz3's proof or approval requirements. Use the model picker's session-only
option when you do not want to change your default.

`/status` describes the Claude session; use the clauz3 `status` tool for proof,
approval, and execution phases. Native `/skills` lists Claude skills, not the
selected inert documentation: use `list_skills` and `read_skill` for that.
Administrator-managed configuration remains trusted and may override the
session's settings.

If Claude prints tool-call markup as ordinary text, that is not an executed
tool call. Use `/mcp` to inspect the clauz3 bridge connection. Exit and relaunch
through `clauz3 start` to rerun its startup checks. When using
a local checkout, use its development environment so source updates take effect
on the next launch. Run this from the project the agent should work in:

```bash
uv run --project /path/to/clauz3 clauz3 start claude
```

For a persistent `clauz3` command backed by your checkout, run
`just install-editable` in that checkout. Source changes take effect on the
next launch; run `just reinstall` there when dependencies or command
definitions change. If the command is not on PATH, run `uv tool update-shell`
and open a new terminal. Keep your everyday checkout on `main` and use the
explicit `uv run --project` command above to test a separate feature checkout.

To test a fresh installed package from that checkout instead, bypass the `uvx`
cache:

```bash
uvx --no-cache --from /path/to/clauz3 clauz3 start claude
```

`--refresh-package clauz3` alone can still reuse an older tool environment for
the same local package version. An already running session also keeps its
original installation; exit it before relaunching. The current startup check
lists `run`, `tools`, `libraries`, `status`, `list_skills`, and `read_skill`.
A banner listing only the first three indicates an older installation.

If a run is backgrounded, that does not mean it proved or reached approval.
Use `status`: only `awaiting_approval` confirms a pending request in the service.
An older session without `status` can spend a long time proving while the
approval queue remains empty. Restart with the current code for the default
30-second proof deadline; resubmitting to the old session can start another
expensive proof without making an approval appear.

### Selected skill documentation

```bash
clauz3 start claude --skills-dir ./.clauz3/skills --skills-dir ~/shared-skills
```

This adds selected documents to the owned `list_skills()` and `read_skill(id)`
interfaces. These interfaces always provide the built-in `clauz3-authoring` guide.
The list contains stable IDs, folder names and previews: frontmatter is returned
as inert text when present, otherwise the beginning of the document is shown.
The full document is read by ID, never by an agent-supplied file path.

Only `SKILL.md` files are selected, and their contents are snapshotted before
launch. Linked references and scripts are not made available implicitly.
Selected documents are not activated as native skills: frontmatter,
`allowed-tools`, hooks, shell substitutions and commands are documentation,
not permissions or executable configuration. These documents can guide the
model; all agent effects still go
through the installed trusted libraries and `clauz3 run`.

Selection refuses nested symlinks, unreadable/non-UTF-8 files, files over
256 KiB, more than 128 documents, more than 2 MiB of document content, or
folder trees with more than 4,096 entries. Previews are limited to 1,024
characters. Duplicate names have distinct IDs; repeated selection of the same
file is deduplicated. Malformed frontmatter is previewed as plain text.
Startup reports the selected document count and warns if no `SKILL.md` files
were found in the selected folders.

`clauz3 install --skills` generates `.clauz3/skills/<domain>/SKILL.md`; select
that folder explicitly with `--skills-dir ./.clauz3/skills`. Installing a
library, selecting its documentation, and enabling native skills are separate
operations. `start` does not enable native skills.

## `clauz3 resume`

```bash
clauz3 resume <session-id> --no-browser
```

On exit, `start` confirms that its approval service stopped and prints a
managed resume command. Resumption opens the recorded project and Claude
conversation with a new service, new credentials and the same restrictions.
Use this command instead of the bare `claude --resume` hint.

Non-secret session metadata lives under the user-wide clauz3 store's `sessions/`
folder (see `install --global` below). It records the conversation ID, project,
model, debug mode, policy and skill-directory selection, plus content hashes.
Approval credentials, private login URLs and skill-document contents are not
stored there. Claude retains its conversation using its normal session storage.

On both start and resume, trusted roots must stay inside the project. The
selection fingerprint reads all Python files under `tools/`, sources under
trusted roots, and their package initializers. Unreadable files, dangling Python
symlinks, or Python symlinks pointing outside the project abort startup, even
when they are outside an individual trusted root. Remove or fix these files
before starting a session; they are not silently omitted from the fingerprint.

Resume refuses changed library
Python sources or trusted-root targets, policy contents,
or selected skill documents. Start a new session to review a changed selection.
A per-conversation process lock prevents simultaneous managed resumes; separate
conversations each own their own approval service. Missing or invalid metadata
fails before starting a service. The original `--unrestricted` choice is retained
and displayed as debug mode when applicable.

`--no-browser`, `--port` and `--prompt` are supported on resume. Service endpoints
and credentials are never recovered from conversation history. Ordinary `start`
sessions persist approval history and remembered decisions in
`approval-history.sqlite` under the user-wide clauz3 store. The **Profile and
history** link lets the operator inspect rules and include or exclude past
human decisions. See [remembered decisions](../how-to/approval-service.md#remember-decisions-across-sessions)
for matching rules and limitations. Explicit `--policy` sessions currently use
that policy alone and retain in-memory history.

## `clauz3 libraries`

List or search available libraries without importing, downloading, or installing
their code:

```bash
clauz3 libraries
clauz3 libraries email
clauz3 libraries file --json
```

Results include bundled stdlib, the packaged curated external registry, and
user-wide installations. Search matches names, descriptions and metadata, with
all search terms required. Each result shows library dependencies, runtime
requirements, visibility, source information, and an install command. The JSON
form is suitable for scripts. External results are an offline catalog shipped
with the package, not a live search of arbitrary repositories.

"Project domains present" (JSON `installed: true`) means all of the entry's
domain directories exist locally; it does not attest their origin or version.
Discovery is an allowed agent operation; installation remains an operator action.

## `clauz3 prove`

Prove guarantees for a target function in one or more entry files. Each entry
is parsed and proved on its own, against the same trusted roots and the same
explicit import roots; without `--import-root`, each entry's default roots come
from its own directory, exactly as when it is proved alone. Files are never
concatenated, so a `main` in one cannot shadow another's.
Proving several entries in one invocation pays interpreter and import start-up
once instead of per file.

Python callers should use `clauz3.prover.prove_path(entry, trusted_roots=...)`.
The obsolete `clauz3.prover.prove_paths(...)` compatibility wrapper has been
removed; it previously accepted only a single-element iterable.

```
clauz3 prove [--trusted-root PATH ...] [--trusted-roots PATH ...]
             [--import-root PATH ...] [--import-roots PATH ...]
             [--allow-import MODULE ...]
             [--target NAME]
             ENTRY [ENTRY ...]
```

| Option | Description |
| --- | --- |
| `ENTRY` | Path to an entry file containing the target function. Repeatable; each entry is proved separately. |
| `--trusted-root` | Trusted package root with effects and contracts. Repeatable. |
| `--trusted-roots` | One or more trusted roots (space-separated). |
| `--import-root` | Root used to locate imports and trusted modules, in supplied order. Explicit roots replace the default roots. It does not authorize nontrusted library source. Repeatable. |
| `--import-roots` | One or more ordered import roots (space-separated), with the same selection rules. |
| `--allow-import` | Module name or dotted prefix permitted beyond the default allowlist. Origin and supported-surface checks still apply. Repeatable. |
| `--target` | Function name to prove (defaults to `main`). |

Without explicit import roots, file-based `prove`, `run`, and `policy-check`
use the current directory followed by the entry file's parent directory.
With either import-root flag, they use the supplied roots in order without
adding those defaults. Include `--import-root .` explicitly when the trusted
package is below the current directory. These roots select project imports;
normal environment paths remain available, subject to origin checks.

Large trusted dependency graphs can require higher process-level
`CLAUZ3_MAX_DEPENDENCY_MODULES` (default `256`) or
`CLAUZ3_MAX_DEPENDENCY_AST_NODES` (default `500000`) values. These positive-integer
settings also apply to `run`, `policy-check`, and `approval-service`; see
[trusted dependency work limits](python-subset.md#trusted-dependency-work-limits).

With one entry, each result line reads `NAME: CONCLUSION ...`; with several,
each line is prefixed with its entry path. The exit status is the worst across
entries: `0` when every entry proves, `1` when any obligation fails or no target
is found, and `2` for a configuration error or an entry that cannot be read or
parsed. An error in one entry does not stop the others from being proved.

Exits non-zero if any proof obligation fails. See the [Python subset
reference](python-subset.md) for what the prover understands and the
[import allowlist](python-subset.md#import-allowlist) for what untrusted
source can import without `--allow-import`.

## `clauz3 run`

Prove a program, submit it to an approval service, and only execute `main`
after receiving an approval receipt. The approval service URL is read from
`CLAUZ3_APPROVAL_SERVICE`, `CLAUZ3_APPROVAL_URL`, or
`.clauz3/approval-service.json`.

```
clauz3 run [--trusted-root PATH ...] [--trusted-roots PATH ...]
           [--import-root PATH ...] [--import-roots PATH ...]
           [--allow-import MODULE ...]
           [--target NAME] [--approval-timeout SECONDS]
           [--step-budget N]
           [PROGRAM]
```

| Option | Description |
| --- | --- |
| `PROGRAM` | Program path, or stdin when omitted or `-`. |
| `--trusted-root` / `--trusted-roots` | Same as `prove`. |
| `--import-root` / `--import-roots` | Same as `prove`. |
| `--allow-import` | Same as `prove`; checked during preflight and execution. |
| `--target` | Function to prove and run (default `main`). |
| `--approval-timeout` | How long to wait for the approval decision. |
| `--step-budget` | Maximum interpreter nodes evaluated (default `1000000`). Exhausting the budget aborts execution; effects already performed are not rolled back. |

The approved receipt is exposed to the executing process as
`CLAUZ3_APPROVAL_RECEIPT`.
For stdin input, the default import root is the current directory; explicit
roots retain the same ordered selection as file input.

## `clauz3 tools`

List trusted tools and contracts visible from this repository.

```
clauz3 tools [--trusted-root PATH ...] [--trusted-roots PATH ...]
             [--import-root PATH ...] [--import-roots PATH ...]
```

Useful for confirming that a trusted layer is discoverable and that its
`@contract` helpers register as expected.

## `clauz3 approval-service`

Start a localhost FastAPI approval service with REST endpoints and a browser
UI.

```
clauz3 approval-service [--host HOST] [--port PORT] [--policy POLICY]
                        [--trusted-root PATH ...] [--trusted-roots PATH ...]
                        [--import-root PATH ...] [--import-roots PATH ...]
                        [--no-auth]
```

| Option | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind host. |
| `--port` | `8765` | Bind port. |
| `--policy` | _(none)_ | JSON approval policy for auto-decisions (see below). |
| `--trusted-root` / `--trusted-roots` | _(none)_ | Operator-authorized trusted roots, in supplied order. No automatic discovery. |
| `--import-root` / `--import-roots` | current directory | Operator-authorized import roots, in supplied order. |
| `--no-auth` | off | Disable the submission and approver credentials for localhost demos. |

With `--policy`, supply at least one explicit trusted-root or import-root flag.
The two ordered root lists configure one authorized layer. A request must select
that exact pair after path canonicalization; a different root, an added root, or
a different order receives HTTP 403 before any trusted Python is imported.
Use the same explicit root flags on the runner to avoid its default entry-parent
import root changing the selection. The operator must control the code reachable
through these roots; the path configuration does not sandbox Python imports.

Authorized requests are evaluated against policy-admin rules before a human is
asked: a match resolves the request as `auto_approved` or `auto_rejected`,
otherwise it stays pending. Without `--policy`, the service leaves decisions to
the user and does not load the request's trusted modules. See
[Approval policies](../todos/approval-policies.md).

See [Run the approval service](../how-to/approval-service.md) for the operational
flow.

## `clauz3 policy-check`

Dry-run an approval policy against a program and report the auto-decision
(`auto_approved`, `auto_rejected`, or `ask`) without starting a service or
executing the submitted program. This is the policy admin's authoring tool: it
runs the same entailment checks the service would, importing trusted modules.
The operator's selected roots authorize this local evaluation.

```
clauz3 policy-check [PROGRAM] --policy POLICY [--target NAME] [--expect DECISION]
```

| Option | Default | Description |
| --- | --- | --- |
| `PROGRAM` | stdin | Program path, or stdin when omitted or `-`. |
| `--policy` | _(required)_ | JSON approval policy to evaluate. |
| `--trusted-root` / `--import-root` | discovered | Roots used to prove the clauses. |
| `--target` | `main` | Function to evaluate. |
| `--expect` | _(none)_ | Exit non-zero unless the decision matches this value. |

See [Approval policies](../todos/approval-policies.md).

## `clauz3 mock-approval-service`

A scripted decision server for tests and demos. The config JSON drives the
returned decision (e.g. `{"decision": "approved_once"}`).

```
clauz3 mock-approval-service --config CONFIG [--host HOST] [--port PORT]
```

| Option | Default | Description |
| --- | --- | --- |
| `--config` | _(required)_ | JSON decision config. |
| `--host` | `127.0.0.1` | Bind host. |
| `--port` | `8765` | Bind port. |

## `clauz3 install`

Copy a trusted `tools/` layer into a project's `.clauz3/tools/` (or the
user-wide store with `--global`).

```
clauz3 install [--into PATH | -g/--global] [--skills] [--force] SOURCE
```

| Option | Description |
| --- | --- |
| `SOURCE` | `stdlib:<name>`, `global:<domain>`, git source, local project containing `tools/`, or a `tools/` folder directly. |
| `--into` | Destination project root (defaults to the current directory). |
| `-g`, `--global` | Store the library for later selection into projects. Mutually exclusive with `--into`. |
| `--skills` | Also generate `.clauz3/skills/<domain>/SKILL.md` (or `skills/<domain>/` in the user-wide store) for each installed domain. |
| `--force` | Overwrite an existing trusted layer at the destination. |

The CLI also installs declared library dependencies, such as `filesystem` for
`grep` and `find`. It preserves dependencies already present in the destination,
even with `--force`; that flag applies to the requested library. Runtime packages
and executables (for example DuckDB or `gh`) must be provisioned separately.

Global installation is a reusable user-wide store:

```bash
clauz3 install -g stdlib:grep
clauz3 libraries global:
clauz3 install global:grep
```

The last command copies the selected library and its declared dependencies into
the current project's `.clauz3/tools/` directory. It does not create symlinks or add
global directories to import/trusted roots. Subsequent global updates do not
change existing project copies. Global libraries also appear in the startup
chooser under individual selection. Private repositories still require access
through the user's git credentials.

The default store is `$XDG_DATA_HOME/clauz3` (or `~/.local/share/clauz3` when
unset). Set `CLAUZ3_HOME` to override the store root. Each copied domain can be
selected as `global:<domain>`; other domains in the store are not copied.

Installed libraries are imported as `tools.<domain>.trusted` from the
project's `.clauz3/` directory. When no `--import-root` is given, commands use
the working directory with its `.clauz3/` ahead of it, whether or not
`.clauz3/` exists yet, so installing a first library does not change an active
session's authorized layer. Explicit `--import-root` values are used exactly as
given. Layers the project authors itself
may stay in a top-level `tools/`.

See [Install trusted layers](../how-to/install-layers.md) for the recommended
workflow.

## `clauz3 lint`

Check a trusted layer's declarations against what its bodies actually do.

```
clauz3 lint PATH [PATH ...]
```

| Option | Description |
| --- | --- |
| `PATH` | A Python file, or a directory whose `*.py` files are all checked. |

This command runs [Deal](https://deal.readthedocs.io/)'s linter with support
for ClauZ3's native condition decorators. It can report:

- `DEL011 pre contract error` when a checked caller passes an argument that
  the callee's `@clauz3.requires(...)` forbids.
- `DEL012 post contract error` when a body returns `-1` under
  `@clauz3.ensures(lambda result: result > 0)`.
- `DEL050 missed marker (syscall)` when a body calls `subprocess.run` but
  its optional `@deal.has(...)` declaration lacks `syscall`.

Contract checks do not require `@deal.has`. Adding it opts into marker
checks; `@clauz3.trusted` alone declares the proof boundary and supplies no
markers. Lint inspects trusted bodies, which the prover skips.

The command checks the original file and, when needed, a temporary copy with
native conditions and aliased Deal decorators rewritten to Deal's spelling
and `predicate=` renamed to `validator=`. The copy is a file so Deal can use
its call-inference engine. Results from both passes are deduplicated and
report the original source lines. Only the file being checked is rewritten:
native or aliased
preconditions on imported callees can still be missed
([known limitation](https://github.com/cmungall/agent-deal/issues/173)).

Exit status is `0` when no violations are found, `1` for violations, and `2`
for a path or parse error. Bundled stdlib tools run lint from their
`tests/Justfile`, and worked examples run it from their example `Justfile`.

## `clauz3 test`

Run a library's bundled test suite by invoking its `tests/Justfile` with
[`just`](https://just.systems). The source is resolved the same way as
`install`.

```
clauz3 test [--recipe RECIPE] SOURCE
```

| Option | Description |
| --- | --- |
| `SOURCE` | A bundled stdlib tool (such as `stdlib:filesystem`), a local project path containing a `tests/` folder, or a `tests/` folder directly. |
| `--recipe` | The `just` recipe to run (default: `test`). |

The command exits with the recipe's exit status, so it composes with CI. The
bundled libraries' tests use `clauz3 prove`/`policy-check`, which are purely
static: the prover records each trusted-effect call as a fact and never runs
the function body, so no real side effects fire. A library whose tests instead
drive `clauz3 run` would execute real effects and should mock them first.

## `clauz3 config`

Configure a repository for clauz3-mediated agent access by writing the default
Claude Code permissions to `.claude/settings.json`: read-only inspection
(`Read`, `Glob`, `Grep`) plus the `clauz3` CLI (`Bash(clauz3:*)`). With these
settings an agent reaches every side-effecting tool through `clauz3`, where the
prover checks its contract first.

```
clauz3 config [--into PATH] [--force]
```

| Option | Description |
| --- | --- |
| `--into` | Project root to configure (defaults to the current directory). |
| `--force` | Overwrite an existing `.claude/settings.json` with the defaults. |

`config` is idempotent: if `.claude/settings.json` already exists, its entries
(including any extra allows and a custom `defaultMode`) are preserved and only
the missing default permissions are merged in. It is the configuration
counterpart to `install`; together they move toward a single command that
ensures a repo is set up and its tools installed.

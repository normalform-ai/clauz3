# Using Pi With ClauZ3

This guide shows how to use [Pi](https://pi.dev) as an agent harness for ClauZ3-backed side effects.

The goal is that an agent does not execute trusted-effect Python directly. Instead it writes a small ClauZ3 program, proves the declared guarantees, submits the proved program for approval, and only then runs it.

## Status

The Pi integration is currently an experimental extension source tree in this repository:

```text
extensions/clauz3/
skills/clauz3/
```

It is intentionally **not** under `.pi/`, so it is not auto-loaded for every developer using Pi in this repo. Load it explicitly while dogfooding.

The intended extracted package name is:

```text
normalform-ai/pi-ext-clauz3
```

or, if published to npm:

```text
@normalform-ai/pi-ext-clauz3
```

Until that package exists, load the extension and skill explicitly from this repo.

## What The Extension Adds

The extension registers these Pi tools:

| Tool | Purpose |
| --- | --- |
| `clauz3_tools` | Discover trusted effects and contract helpers under `tools/*/trusted`. |
| `clauz3_prove` | Prove a complete inline ClauZ3 program without executing it. |
| `clauz3_run` | Prove, request approval for, and run a complete inline ClauZ3 program. |

It also registers these slash commands:

| Command | Purpose |
| --- | --- |
| `/clauz3-tools` | Show trusted effects and contract helpers. |
| `/clauz3-status` | Show ClauZ3 command discovery, trusted roots, import roots, and approval-service status. |

When the current project looks ClauZ3-aware, the extension also adds system-prompt guidance telling the agent to use `clauz3_prove` and `clauz3_run` rather than direct Python execution for trusted side effects.

## Requirements

Install Pi:

```bash
npm install -g @earendil-works/pi-coding-agent
```

Install ClauZ3 development dependencies in this repo:

```bash
uv sync --dev
```

If running Pi from inside this checkout, the extension auto-detects the repo root and uses:

```bash
uv run --project <repo-root> clauz3
```

If running Pi from another user repo, either install `clauz3` on `PATH` or point the extension at this checkout:

```bash
export CLAUZ3_PROJECT=/Users/cjm/repos/clauz3
```

For a fully custom command, set:

```bash
export CLAUZ3_PI_COMMAND='uv run --project /Users/cjm/repos/clauz3 clauz3'
```

`CLAUZ3_PI_COMMAND` takes precedence over `CLAUZ3_PROJECT`.

## Start An Approval Service

`clauz3_run` requires an approval service. The service is user-controlled; the agent should not start or replace it unless explicitly asked.

For a local demo:

```bash
cd examples/email
uv run --project ../.. clauz3 approval-service --port 8891
```

The service prints:

```text
CLAUZ3_APPROVAL_SERVICE=http://127.0.0.1:8891
```

In the terminal where Pi will run, export that value:

```bash
export CLAUZ3_APPROVAL_SERVICE=http://127.0.0.1:8891
```

Open the browser UI at:

```text
http://127.0.0.1:8891/
```

When `clauz3_run` submits a request, open that UI, inspect the guarantees and
expandable source, then approve or reject the pending request.

## Use The Extension From This Checkout

From this repository, load the extension explicitly:

```bash
cd examples/email
export CLAUZ3_APPROVAL_SERVICE=http://127.0.0.1:8891
pi \
  -e ../../extensions/clauz3/index.ts \
  --skill ../../skills/clauz3/SKILL.md
```

This avoids surprising developers who use Pi for normal ClauZ3 repo maintenance: the extension is available for testing, but it is not automatically active unless requested.

Then ask something like:

```text
Send an email to bob@example.com saying: Report is ready. Use ClauZ3 and make the permission contract explicit.
```

Expected behavior:

1. The agent inspects trusted tools, if needed.
2. The agent writes an inline ClauZ3 program.
3. The agent attaches guarantees such as:

   ```python
   @clauz3.guarantee(emails.only(["bob@example.com"]))
   @clauz3.guarantee(emails.unique_recipients())
   ```

4. The agent proves the program.
5. The agent submits it through `clauz3_run`.
6. The approval service records the request and returns a receipt.
7. The target `main` executes only after approval.

## Quick Manual Checks

Inside Pi, run:

```text
/clauz3-status
```

Then:

```text
/clauz3-tools
```

From `examples/email`, `/clauz3-tools` should show entries like:

```text
effect email/trusted/effects.py:send_email(addr, msg)
contract email/trusted/contracts.py:only(addresses)
contract email/trusted/contracts.py:unique_recipients()
```

## Non-Interactive Smoke Test

You can also smoke-test the ClauZ3 side without Pi:

```bash
cd examples/email
CLAUZ3_APPROVAL_SERVICE=http://127.0.0.1:8891 \
uv run --project ../.. clauz3 run <<'PY'
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["bob@example.com"]))
@clauz3.guarantee(emails.unique_recipients())
def main() -> None:
    send_email("bob@example.com", "Report is ready")
PY
```

Expected output includes:

```text
main: proved! assertion, guarantee, guarantee
approval: approved_once receipt=...
executed: main
```

## Running In Another User Repo

A ClauZ3-enabled user repo should have trusted roots like:

```text
tools/email/trusted/effects.py
tools/email/trusted/contracts.py
```

Then run Pi from the user repo with a command pointing at ClauZ3:

```bash
cd /path/to/user-repo
export CLAUZ3_PROJECT=/Users/cjm/repos/clauz3
export CLAUZ3_APPROVAL_SERVICE=http://127.0.0.1:8891
pi \
  -e /Users/cjm/repos/clauz3/extensions/clauz3/index.ts \
  --skill /Users/cjm/repos/clauz3/skills/clauz3/SKILL.md
```

The extension discovers trusted roots as `tools/*/trusted` below the current working directory. Import roots default to the current working directory.

## Future Package Installation

Once extracted to a standalone repo, install from GitHub:

```bash
pi install git:github.com/normalform-ai/pi-ext-clauz3
```

Or project-locally:

```bash
pi install -l git:github.com/normalform-ai/pi-ext-clauz3
```

If published to npm:

```bash
pi install npm:@normalform-ai/pi-ext-clauz3
```

A standalone package should have this shape:

```text
pi-ext-clauz3/
  package.json
  extensions/clauz3/index.ts
  skills/clauz3/SKILL.md
  README.md
```

with a Pi manifest like:

```json
{
  "name": "@normalform-ai/pi-ext-clauz3",
  "keywords": ["pi-package"],
  "pi": {
    "extensions": ["./extensions"],
    "skills": ["./skills"]
  }
}
```

## Agent Policy

The extension guidance intentionally says:

- use `clauz3_tools` to inspect capabilities;
- use `clauz3_prove` while iterating;
- use `clauz3_run` for execution;
- do not run trusted-effect programs directly with `python`;
- do not start, replace, or reconfigure the approval service unless explicitly asked.

This is guidance, not yet a hard enforcement gate. A future version may add an optional strict mode that blocks direct Python execution of programs importing trusted effects.

## Troubleshooting

### `clauz3` not found

Set one of:

```bash
export CLAUZ3_PROJECT=/path/to/clauz3-checkout
export CLAUZ3_PI_COMMAND='uv run --project /path/to/clauz3-checkout clauz3'
```

### No trusted tools found

Run Pi from the user repo root, or pass explicit trusted/import roots through the `clauz3_*` tools. The default discovery pattern is:

```text
tools/*/trusted
```

### No approval service configured

Start a user-controlled approval service and export:

```bash
export CLAUZ3_APPROVAL_SERVICE=http://127.0.0.1:<port>
```

### The agent proposes weak guarantees

Ask it to strengthen the contract. For example:

```text
Prove that only Bob is emailed and nobody is emailed twice.
```

The approval artifact is the verified contract, not the agent's prose summary.

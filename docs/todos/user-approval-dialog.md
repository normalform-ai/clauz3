# User Approval Dialog Design

## Status

Draft design note. This belongs in `docs/todos/` because it describes a product
and architecture direction that has not been implemented yet.

Initial slice implemented: `clauz3 run` reads a complete program from stdin or a
file, proves `main`, submits a JSON approval request to an externally configured
approval service, requires an approval receipt, and then executes `main`.
`clauz3 approval-service` starts a localhost FastAPI service with a browser UI
for user decisions. `clauz3 mock-approval-service --config config.json` remains
available for tests. Trusted functions do not yet verify receipts themselves.

## Problem

An agent proposes a plan and a Python program. The program includes contracts
that describe what it guarantees about trusted side effects. The agent is free
to provide weak contracts, including no guarantees, but should be incentivized
to provide useful, specific guarantees.

The user needs a way to approve, reject, or ask for a stronger explanation or
contract before side effects run. This decision should not be controlled only by
the agent's normal tool-calling transcript.

## Core Invariant

User approval must live outside the agent-controlled execution path, and trusted
side effects must require a fresh approval receipt matching the proven request.

The dialog is user experience. The receipt check is enforcement.

## Actors

- Agent: writes a plan, program, and contracts.
- `clauz3` CLI: statically analyzes the program and submits approval
  requests.
- Local approval service: owns request records and user decisions.
- User UI: text UI or browser UI connected to the local approval service.
- Trusted functions: side-effectful operations that only execute with a valid
  receipt.

## High-Level Flow

1. Agent writes a Python program against trusted functions.
2. Agent attaches contracts or guarantees to `main`.
3. Agent invokes `clauz3 run ...`.
4. CLI analyzes the program and compares proven facts to saved policy.
5. If hard policy is violated, the request is rejected.
6. If policy fully covers the proven behavior, the request can auto-pass.
7. Otherwise the request is sent to the local approval service.
8. User reviews a deterministic summary, the contracts, and optionally the
   source program.
9. User chooses approve, reject, or requests more information/stronger
   guarantees with free-form feedback.
10. On approval, the service issues a short-lived receipt.
11. Trusted functions check the receipt before performing side effects.

## CLI Shape

Example:

```bash
clauz3 run <<'PY'
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    send_email("bob@example.com", "Report is ready")
PY
```

Expected output:

```text
main: proved! assertion, guarantee
approval: approved_once receipt=mock-clr_8f32c1
executed: main
```

The CLI should not ask the user directly in the agent transcript. It routes
decisions through the local service configured outside the agent-controlled
command. The current implementation reads that service from
`CLAUZ3_APPROVAL_SERVICE`, `CLAUZ3_APPROVAL_URL`, or
`.clauz3/approval-service.json`.

Start the simple local service in a separate terminal:

```bash
clauz3 approval-service
```

It exposes REST endpoints at `POST /requests`, `GET /requests`, and
`GET /requests/{request_id}`. Open `http://127.0.0.1:8765/` for the simple UI,
which lists pending requests, shows details, and records user decisions.

## Request Record

Each request should persist enough information to audit and re-check the exact
decision:

- request id
- program text
- program hash
- trusted functions loaded
- logic modules loaded
- declared contracts
- generated facts/effects
- proof result
- policy comparison
- deterministic natural-language summary
- agent-provided rationale, separated from verified facts
- user decision
- optional user feedback text
- issued receipt id, if approved

## User Dialog

The user should see verified facts before agent prose.

Example:

```text
Request: Run proposed email program

Policy result:
? User approval required

Verified guarantees:
✓ Proven: every email recipient is in ["bob@example.com"]
✓ Proven: no filesystem writes
✗ Not proven: no recipient is emailed more than once
? Unknown: email body contents

Trusted actions observed:
- send_email(addr="bob@example.com", msg="...")

Agent explanation:
"I need to notify Bob that the report is ready."

Actions:
[Approve once] [Approve and remember] [Reject] [Ask / request changes]

Question or feedback:
> tell me what files will be written
```

The user must always have a free-text feedback box. This is not only for
rejection. It should be available from every non-terminal state, including when
the request looks approvable.

## Free-Text Feedback

Free-text feedback supports several cases:

- Asking for a deterministic explanation:
  - "tell me what files will be written"
  - "who will be emailed?"
  - "what tables are read?"
- Requesting a stronger contract:
  - "prove that no one is emailed twice"
  - "guarantee no files outside ~/Sandbox are written"
- Asking the agent to revise the plan:
  - "send only to Bob, not Ann"
  - "write the report under ~/Sandbox/reports"
- Rejecting with rationale:
  - "do not email external addresses"

The approval service should classify feedback into one of two broad paths:

- Local answer: answer from proof facts, contracts, policy, and source analysis.
- Agent revision: return structured feedback to the agent and require a new
  request.

For example:

```json
{
  "decision": "request_more",
  "request_id": "adr_8f32c1",
  "feedback": "tell me what files will be written",
  "required_response": "answer_from_facts_or_resubmit"
}
```

If the answer cannot be derived deterministically from the proof record, the
service should say so and ask the agent to resubmit with stronger guarantees.

## Policy Interaction

Policy should be checked before asking the user:

- Hard deny: reject without approval.
- Fully covered by policy and proof: auto-pass, optionally notify.
- Not covered by policy: ask user.
- No relevant guarantee: treat as unknown, not safe.

Example policy:

```text
never write outside ~/Sandbox
ask before sending email
auto-approve email only to bob@example.com
```

If static analysis proves all writes are under `~/Sandbox`, that part is safe.
If the contract says nothing about file writes, the request should show file
writes as unknown rather than assume none.

## Execution Model

The receipt-enforcement section below assumes the agent never shares a process
with the trusted layer. The agent writes the program; `clauz3 run` executes
it. The agent's harness exposes no arbitrary-code-execution tool, so the agent
itself cannot monkey-patch a trusted import or shadow the wrapper from inside
the interpreter that runs the program — it is a text producer.

That assumption only holds if the program the agent writes also cannot reach
side effects through a route other than the trusted layer. The current
prototype has a conservative source-level blocklist for obvious direct imports
and reflective builtins, plus a reduced builtins mapping when `clauz3 run`
executes the program. This is useful for the mock flow, but it is not yet a
complete Python sandbox or a transitive import policy.

The target design is stronger and should have two layers.

**Static check at prove time.** The prover should reject any program whose
transitively-imported modules are not in the per-project allowlist. The
allowlist is the trusted layer plus a curated pure-stdlib subset (`json`,
`dataclasses`, `math`, ...). Imports of `smtplib`, `requests`, `subprocess`,
`ctypes`, `socket`, dynamic `__import__`, `importlib`, `eval`, and `exec` should
be static failures, surfaced to the agent like any contract violation. The
agent sees the rejection and resubmits.

**Import sandbox at run time.** `clauz3 run` should install an import hook that
refuses non-allowlisted modules even when reached reflectively (string-built
module names, `globals()["__builtins__"]["__import__"]`, loaders bundled into
deserialized payloads). Defense in depth for the static check.

Within the program, monkey-patching a trusted symbol (`send_email = lambda
*a: None`) only rebinds the local name. The real side effect lives inside the
trusted module's implementation; the program cannot reach the underlying
capability without going through the wrapper. Shadowing prevents the call
from happening; it cannot cause one to happen around the wrapper.

The trusted computing base is therefore: the trusted layer and its real
implementations, the prover, `clauz3 run` and its import hook, and the
harness configuration that fixes the agent's tool surface. The agent is
outside the TCB.

## Receipt Enforcement

Approval is meaningful only if trusted side effects require a receipt.

Trusted functions should verify:

- receipt exists
- receipt is not expired
- receipt matches the program hash or request id
- receipt permits the specific trusted operation
- receipt permits the specific bound arguments, or a proven superset

Example:

```text
send_email(addr="bob@example.com", msg="...") requires receipt allowing:
  marker=email
  function=send_email
  addr in ["bob@example.com"]
```

An agent should not be able to bypass the dialog by calling the real
implementation directly. The trusted wrapper must be the only available route to
the side effect in the execution environment.

## Out-Of-Harness UI

Some harnesses can embed the dialog directly. Others, such as generic coding
agents, cannot be trusted to show a faithful approval dialog because the agent
controls the tool call and output stream.

For those cases, use a local service:

```bash
clauz3 daemon
```

Possible UIs:

- terminal UI connected to the daemon
- localhost browser UI
- simple notification that opens the browser to the request
- future richer UI integrations

The agent-facing CLI should block until the daemon records a decision or a
timeout occurs.

## Auto-Pass And User Visibility

Auto-pass should still be auditable. Even if the user is not interrupted, the
service should record:

- why the request passed
- which policy rule allowed it
- which proof obligations succeeded
- which receipt was issued

Depending on settings, the user may want a low-noise notification stream:

```text
Auto-approved adr_8f32c1:
  emails only to bob@example.com
  no file writes
```

## Deterministic Natural Language

The approval UI should generate its own explanation from structured proof data.
Agent prose can be shown, but must be visually separate from verified facts.

Good:

```text
Verified: every email recipient is in ["bob@example.com"].
Agent says: "I will only email Bob."
```

Bad:

```text
The agent says this is safe.
```

## Decision Types

- `approved_once`: issue receipt for this exact request.
- `approved_remember`: add or propose a durable policy rule.
- `rejected`: do not issue receipt.
- `request_more`: return feedback to the agent or answer locally.
- `expired`: no decision in time.
- `auto_approved`: policy plus proof was sufficient.
- `auto_rejected`: hard policy violation or unsupported proof.

## Open Questions

- What is the minimal receipt format?
- Should receipts be files, daemon-held capabilities, or signed tokens?
- How much source browsing is needed in the first UI?
- Should local deterministic answers use only facts, or can they also inspect
  source?
- How should "approve and remember" propose durable policy changes safely?
- How should the daemon authenticate local clients on a multi-user machine?
- What should the agent receive for `request_more`: raw user text, structured
  requirements, or both?
- What stdlib subset is safe enough to allowlist by default for the import
  check, and where does the per-project allowlist live?
- How should the static import check handle dynamically loaded code (plugin
  loaders, `pkgutil.iter_modules`) — refuse them entirely, or require the
  loader itself to be in the trusted layer?

# Approval Service

`clauz3 run` has a separate approval step between proof and execution. The
approval service is the user-controlled process that receives the proved
program, shows the contract and code, records the user's decision, and returns
that decision to `clauz3 run`.

The important trust-boundary rule is that the agent should not own this process.
An agent may write the program and guarantees, but a user or harness should
start and inspect the approval service.

## Runtime flow

`clauz3 run` follows this order:

1. Parse and prove the requested target, `main` by default.
2. Stop immediately if any proof fails. No approval request is submitted.
3. Build a JSON approval request containing the program, declared guarantees,
   proof summaries, trusted roots, import roots, and program hash.
4. `POST` that request to the configured approval service.
5. The approval service records the request as pending and shows it in the UI.
6. The user approves or rejects the request, optionally with a reason.
7. The original `POST` returns that decision to `clauz3 run`.
8. `clauz3 run` executes the target only if the decision is approved and has a
   receipt.

The approval artifact is the verified contract, not the agent's prose summary.
The program source is included for inspection and shown behind an expandable
section in the bundled UI.

## Current local service

The bundled `clauz3 approval-service` is a small localhost FastAPI service with
a REST API and browser UI. It keeps requests and decisions in memory. It is
meant for local demos, integration tests, and harness-controlled approval
experiments.

It is not a hardened network service. Bind it to `127.0.0.1` unless you have
added your own authentication, authorization, storage, and network controls.

## Start the service

From a project or example directory:

```bash
uv run clauz3 approval-service --host 127.0.0.1 --port 8765
```

Command-line arguments:

| Argument | Default | Meaning |
| --- | --- | --- |
| `--host HOST` | `127.0.0.1` | Host address to bind. Use localhost for normal use. |
| `--port PORT` | `8765` | TCP port to listen on. |

There is no decision config for the real approval service. Decisions come from
the user through the UI or the decision API.

On startup the service prints the environment variable value that `clauz3 run`
can use:

```text
CLAUZ3_APPROVAL_SERVICE=http://127.0.0.1:8765
```

Open the browser UI at:

```text
http://127.0.0.1:8765/
```

## User decisions

The bundled UI offers these decision paths:

| Decision | Meaning |
| --- | --- |
| `approved_once` | Approve this exact request and issue a receipt. |
| `rejected_contract` | Reject because the proved contract says the program will do something the user does not want. |
| `request_more` | Reject for now because the contract or explanation is too vague. The agent should revise and submit a new request. |
| `rejected` | Reject without a more specific subtype. |

Only these approved decisions let `clauz3 run` execute:

- `approved_once`
- `approved_remember`
- `auto_approved`

All other decisions are treated as not approved. The current UI issues
`approved_once`; future policy layers may issue `approved_remember` or
`auto_approved`.

The optional reason field is stored as `feedback` in the JSON response. It is
user-authored decision text, not startup config.

## REST and UI routes

| Route | Method | Purpose |
| --- | --- | --- |
| `/health` | `GET` | Health check. |
| `/` or `/ui` | `GET` | Browser request list. |
| `/requests` or `/api/requests` | `POST` | Submit an approval request and wait for a decision. |
| `/requests` or `/api/requests` | `GET` | List recorded requests. |
| `/requests/{request_id}` or `/api/requests/{request_id}` | `GET` | Fetch one recorded request. |
| `/requests/{request_id}/decision` or `/api/requests/{request_id}/decision` | `POST` | Record the user decision. |
| `/ui/requests/{request_id}` | `GET` | Browser detail view for one request. |

The decision API accepts JSON:

```json
{
  "decision": "request_more",
  "feedback": "The contract should say exactly which files will be written."
}
```

For approved decisions, the service returns a receipt. If the decision API does
not provide one, the local service generates `local-<request_id>`.

## Connect `clauz3 run`

`clauz3 run` discovers the approval-service URL in this order:

1. `CLAUZ3_APPROVAL_SERVICE`
2. `CLAUZ3_APPROVAL_URL`
3. `.clauz3/approval-service.json` in the current working directory

The recommended path for interactive use is to export the value printed by the
service:

```bash
export CLAUZ3_APPROVAL_SERVICE=http://127.0.0.1:8765
```

For a repo-local discovery file:

```bash
mkdir -p .clauz3
printf '%s\n' '{"url": "http://127.0.0.1:8765"}' > .clauz3/approval-service.json
```

The `.clauz3/approval-service.json` file must contain a non-empty string `url`.
It is read by `clauz3 run`; it is not passed to `clauz3 approval-service`.

Example run from `examples/email`:

```bash
uv run --project ../.. clauz3 run cases/only_bob_pass.py
```

`clauz3 run` waits for user approval. The default wait is 300 seconds and can be
changed with:

```bash
uv run clauz3 run --approval-timeout 600 plan.py
```

If no approval-service URL is configured, `clauz3 run` exits with:

```text
error: no approval service configured; set CLAUZ3_APPROVAL_SERVICE
```

## Mock approval service

`clauz3 mock-approval-service` is the config-driven service. Use it for tests,
automation, and local demos where no user will click the UI.

Start it with a JSON decision config:

```bash
printf '%s\n' '{"decision": "approved_once"}' > approval.json
uv run clauz3 mock-approval-service --config approval.json --port 8765
```

Mock command-line arguments:

| Argument | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--config PATH` | yes | none | JSON decision config described below. |
| `--host HOST` | no | `127.0.0.1` | Host address to bind. |
| `--port PORT` | no | `8765` | TCP port to listen on. |

Mock config keys:

| Key | Type | Meaning |
| --- | --- | --- |
| `decision` | string | Decision returned to `clauz3 run`. Defaults to `approved_once` when omitted. |
| `receipt` | string | Optional receipt returned for approved decisions. Defaults to `mock-<request_id>` when omitted. |
| `feedback` | string | Optional mock feedback returned with the decision. |
| `require_program_sha256` | string | Optional exact program hash gate. If the request hash does not match, the mock returns `rejected`. |

Example mock denial:

```json
{
  "decision": "request_more",
  "feedback": "Add a guarantee that recipients are unique."
}
```

The mock server supports `POST /requests` and prints
`CLAUZ3_APPROVAL_SERVICE=...` on startup, but it does not provide the FastAPI
browser UI or request-list routes.

## Protocol shape

A `clauz3 run` request looks like this:

```json
{
  "schema_version": 1,
  "kind": "clauz3.run",
  "request_id": "clr_...",
  "program_sha256": "...",
  "source_name": "cases/only_bob_pass.py",
  "target": "main",
  "trusted_roots": ["tools/email/trusted"],
  "import_roots": ["."],
  "guarantees": ["emails.only(['bob@example.com'])"],
  "proofs": [
    {
      "name": "main",
      "conclusion": "proved!",
      "description": "assertion, guarantee"
    }
  ],
  "program": "..."
}
```

The service response must be a JSON object with at least a string `decision`:

```json
{
  "decision": "approved_once",
  "request_id": "clr_...",
  "receipt": "local-clr_..."
}
```

For approved decisions, `clauz3 run` requires a receipt before it executes the
target. For not-approved decisions, `clauz3 run` exits without executing and
prints the decision plus any feedback.

## Troubleshooting

`error: no approval service configured; set CLAUZ3_APPROVAL_SERVICE`

: The service URL is not visible to `clauz3 run`. Export the printed
  `CLAUZ3_APPROVAL_SERVICE=...` value, or create
  `.clauz3/approval-service.json` with a `url` key.

`approval service is unavailable`

: The approval service is not running, the URL points at the wrong port, or the
  service stopped before the user decision was recorded.

`approval: request_more`

: The user or mock service responded with a not-approved decision. Read the
  printed feedback or inspect the request in the browser UI, strengthen the
  program guarantees, and run again.

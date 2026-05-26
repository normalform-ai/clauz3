# CLI reference

The `clauz3` command is the single entry point for the prover, the approval
service, the runner, and the trusted-layer installer. This page lists the
subcommands and their flags. Run `clauz3 <subcommand> --help` for the
authoritative output from your installed version.

## `clauz3`

```
clauz3 [--version] {prove,run,tools,approval-service,policy-check,mock-approval-service,install,config} ...
```

Static contract proofs for agent-authored Python.

## `clauz3 prove`

Prove guarantees for a target function in an entry file.

```
clauz3 prove [--trusted-root PATH ...] [--trusted-roots PATH ...]
             [--import-root PATH ...] [--import-roots PATH ...]
             [--target NAME]
             ENTRY
```

| Option | Description |
| --- | --- |
| `ENTRY` | Path to the entry file containing the target function. |
| `--trusted-root` | Trusted package root with effects and contracts. Repeatable. |
| `--trusted-roots` | One or more trusted roots (space-separated). |
| `--import-root` | Extra root added to `sys.path` for normal imports. Repeatable. |
| `--import-roots` | One or more import roots (space-separated). |
| `--target` | Function name to prove (defaults to `main`). |

Exits non-zero if any proof obligation fails. See the [Python subset
reference](python-subset.md) for what the prover understands.

## `clauz3 run`

Prove a program, submit it to an approval service, and only execute `main`
after receiving an approval receipt. The approval service URL is read from
`CLAUZ3_APPROVAL_SERVICE`, `CLAUZ3_APPROVAL_URL`, or
`.clauz3/approval-service.json`.

```
clauz3 run [--trusted-root PATH ...] [--trusted-roots PATH ...]
           [--import-root PATH ...] [--import-roots PATH ...]
           [--target NAME] [--approval-timeout SECONDS]
           [PROGRAM]
```

| Option | Description |
| --- | --- |
| `PROGRAM` | Program path, or stdin when omitted or `-`. |
| `--trusted-root` / `--trusted-roots` | Same as `prove`. |
| `--import-root` / `--import-roots` | Same as `prove`. |
| `--target` | Function to prove and run (default `main`). |
| `--approval-timeout` | How long to wait for the approval decision. |

The approved receipt is exposed to the executing process as
`CLAUZ3_APPROVAL_RECEIPT`.

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
```

| Option | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind host. |
| `--port` | `8765` | Bind port. |
| `--policy` | _(none)_ | JSON approval policy for auto-decisions (see below). |

With `--policy`, each request is evaluated against policy-admin rules before a
human is asked: a match resolves it as `auto_approved` or `auto_rejected`,
otherwise it stays pending. See
[Approval policies](../todos/approval-policies.md).

See [Run the approval service](../how-to/approval-service.md) for the operational
flow.

## `clauz3 policy-check`

Dry-run an approval policy against a program and report the auto-decision
(`auto_approved`, `auto_rejected`, or `ask`) without starting a service or
executing anything. This is the policy admin's authoring tool: it runs the same
entailment checks the service would.

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

Copy a trusted `tools/` layer from one project into another.

```
clauz3 install [--into PATH] [--skills] [--force] SOURCE
```

| Option | Description |
| --- | --- |
| `SOURCE` | Local project path containing a `tools/` folder, or a `tools/` folder directly. |
| `--into` | Destination project root (defaults to the current directory). |
| `--skills` | Also generate `agents/skills/<domain>/SKILL.md` for each installed domain. |
| `--force` | Overwrite an existing trusted layer at the destination. |

See [Install trusted layers](../how-to/install-layers.md) for the recommended
workflow.

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

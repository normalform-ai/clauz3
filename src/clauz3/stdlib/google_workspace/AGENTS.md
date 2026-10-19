# Agent Guide

This bundle provides Google Workspace services through clauz3, currently Gmail
via gog. Install `stdlib:google_workspace` and select `google_workspace/gmail`.
Read the public API with `tools(query="gmail")` before submitting a program.
Future Sheets and Docs services belong beside Gmail with separate trusted roots
and contracts; do not enable sibling services implicitly. The old `tools.email`
namespace is not provided. Keep Google-specific setup in the manifests.

## Drafts, previews, and sending

- **Draft an email / send a draft:** use `create_draft(to, subject, body)`.
  It saves an unsent message in Gmail's Drafts folder via
  `gog gmail drafts create`. Include `drafts_only()` in the guarantees.
- **Preview:** `send_email(..., dry_run=True)` is a no-op. It neither saves a
  draft nor sends mail. Print the proposed content if a preview is requested.
- **Send:** `send_email(..., dry_run=False)` delivers immediately. Use only
  when the user requests delivery. Approval to create a draft is not approval
  to send it. Do not switch a draft request to sending if draft creation fails.

A `[DRAFT]` subject is only text. It does not change delivery behavior.
Report a saved draft only after the draft-create run succeeds; report errors
without claiming it was saved. The CLI prints the draft identifiers.

## Save a Gmail draft

```python
import clauz3
from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import create_draft


@clauz3.guarantee(email.drafts_only())
@clauz3.guarantee(email.only_recipients(["alice@example.com"]))
@clauz3.guarantee(email.emails_at_most(1))
def main() -> None:
    create_draft("alice@example.com", "hello", "hello from clauz3")
```

`create_draft` requires the `gog` backend (the default). An unsupported backend,
including `pa`, raises an error before executing any command. It never falls
back to sending. Gmail authorization is managed by gog.

## Contracts

```python
email.drafts_only()                  # forbid all send_email calls, allow drafts
email.no_emails()                    # same no-send guarantee, including dry runs
email.no_drafts()                    # forbid create_draft calls
email.dry_run_only()                 # every send call explicitly dry_run=True
email.only_recipients(allowlist)     # applies to both sends and drafts
email.unique_recipients()            # at most one send or draft per recipient
email.emails_at_most(count)          # combined send + draft call count
email.subject_must_contain(marker)   # text check on sends and drafts
email.subject_length_at_most(n)
email.body_length_at_most(n)
email.no_body_contains(substring)    # banned literal in sent or draft bodies
email.no_subject_contains(substring)
email.no_guarantees()                # explicit null contract
```

These guarantees cover the email library. Other libraries require their own
contracts. Content guards check the specified literal, not arbitrary secrets.

## Maintaining public documentation

Use `@clauz3.trusted` for effect boundaries and `@clauz3.requires(...)` for
preconditions; match the effect's parameter kinds and defaults exactly.
Keep a concise first-line docstring and fuller scope documentation on every
public effect and contract. Give each contract a `description` template for
approval cards. Explicitly document whether previews, drafts, and sends count,
and whether a restriction applies per call or per run.

Clauz3 reads the docstrings for tool discovery and approval details. Installing
with `--skills` also copies first-line summaries into the generated skill.
Reinstall the local library into the consumer project after changing it.
The root `clauz3.yaml` owns shared gog installation and account selection.
`tools/google_workspace/gmail/clauz3.yaml` owns the Gmail summary, service authorization, and
effect-specific setup. Installation generates a `.clauz3-package.yaml`
snapshot beside the installed domain manifest; edit the source manifests and
reinstall instead of maintaining that snapshot. Keep OAuth credentials and
tokens in gog's storage, not in manifests. The central registry lists only
source references. Setup instructions are not executable hooks.

## Preview discipline

Always pass `dry_run` explicitly to `send_email`. The runtime default is
`True`, but default kwargs are not reflected into recorded effect facts.
A program relying on the default while stating `dry_run_only()` fails closed
at proof time. For a preview with no email-side writes, combine
`dry_run_only()` and `no_drafts()`.

```python
@clauz3.guarantee(email.dry_run_only())
@clauz3.guarantee(email.no_drafts())
@clauz3.guarantee(email.only_recipients(["alice@example.com"]))
def main() -> None:
    send_email("alice@example.com", "hello", "hello", dry_run=True)
```

Both effects require a recipient containing `@` and a nonempty subject.
Tests prove programs without executing them, or mock subprocess calls. Never
use a real send or mailbox write to run the test suite.

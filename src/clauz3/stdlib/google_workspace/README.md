# Google Workspace

The Google Workspace bundle currently saves Gmail drafts and previews or sends messages
through gog. Its source, tests, and setup metadata live in Clauz3's stdlib;
the separate `clauz3-tools-assistant` repository is no longer the maintained
source. Gmail's public imports are under `tools.google_workspace.gmail.trusted`.

One install provides the bundle's service domains. Each service has its own
trusted root, contracts, manifest, and conversation selection. Gmail is
`google_workspace/gmail`; future Sheets and Docs services will be siblings,
not separate install packages. Installing or updating the bundle does not
automatically enable services in a workspace conversation.

Google Workspace integrations belong here for now. This library implements
Gmail through **gog**, not the separate **gws** CLI. Calendar, Drive, attachments,
and sending an existing draft by ID are not currently supported.

## Install or migrate

```bash
clauz3 install stdlib:google_workspace --skills
```

For a project with an older `email` copy, finish or stop active work, remove
**email** in Libraries (removal preserves a backup), then install **Google
Workspace**. The installer refuses to place the two versions side by side:
they define the same effect names. `--force` does not bypass this check.
For CLI migration, move `.clauz3/tools/email/` outside the active `tools/` tree
as a backup before installing the new bundle. Also archive the old generated
`.clauz3/skills/email/` guide if present. Project-authored `tools/email/` copies
must likewise be moved out of the active tools tree.

Update consumer imports from `tools.email.trusted` to
`tools.google_workspace.gmail.trusted`. Contract and effect function names
are unchanged. There is no `tools.email` compatibility copy. Start a new
conversation and select `google_workspace/gmail`; old conversation selections
and approval history are not silently rewritten.

For subsequent updates of the renamed bundle:

```bash
clauz3 install stdlib:google_workspace --force --skills
```

This updates `.clauz3/tools/google_workspace/gmail/` and regenerates its API skill
under `.clauz3/skills/google_workspace/gmail/`. Preserve local customizations
before overwriting. In Libraries, review the update on the **Google Workspace**
card. Restart an existing CLI session after an
external install; saved approvals and transcripts retain their original code
and descriptions. Global copies are independent: back up and remove any old
`email` domain in the global store, then install with
`clauz3 install --global stdlib:google_workspace --skills`. Select its Gmail
service into a project with `clauz3 install global:google_workspace/gmail --skills`.

The library ships in the Clauz3 distribution, so installation requires no
assistant-repository checkout or GitHub credentials. Installation does not
install gog, authenticate Google, or grant permission to execute a program.

## User setup

For real drafts and sends, install gog and authorize the intended account
following its [quickstart](https://gogcli.sh/quickstart.html). The executable
must be on the PATH used to launch Clauz3. Configure gog's OAuth client and
Gmail authorization in your own terminal/browser, and select the account using
gog's configuration or `GOG_ACCOUNT`. Workspace administrators may need to
permit access. Keep credentials and tokens in gog's storage; do not put them
in manifests or chat messages. Reauthorize through gog if access is revoked.

`clauz3.yaml` owns CLI installation and account selection requirements;
`tools/google_workspace/gmail/clauz3.yaml` owns Gmail-specific authorization and backend setup.
The Libraries UI and generated skills display that metadata. Installation
snapshots shared requirements beside the domain manifest; edit the source
manifests, not generated snapshots. Setup guidance is descriptive and is
never executed automatically.

Leave `CLAUZ3_EMAIL_BACKEND` unset, or set it to `gog`. The legacy `pa` backend
remains supported for sending only and requires its own setup. Draft creation
rejects other backends without falling back to sending. A send preview needs
neither a CLI nor Google authentication because it invokes no subprocess.

## Draft, preview, or send

| Intent | Effect | Mailbox change |
|---|---|---|
| Save a draft | `create_draft(to, subject, body)` | Creates an unsent Gmail draft |
| Preview | `send_email(to, subject, body, dry_run=True)` | None; print content separately to display it |
| Deliver a new message | `send_email(to, subject, body, dry_run=False)` | Sends immediately |

Both effects require `to` to contain `@` and a nonempty subject. These checks
do not validate mailbox existence. The recipient argument is passed to the
backend unchanged; allowlists compare exact strings, not parsed addresses.
CLI errors propagate. Both effects return `None`; gog output, including draft
identifiers, appears in the run log. A `[DRAFT]` subject changes only text.

```python
import clauz3
from tools.google_workspace.gmail.trusted import contracts as mail
from tools.google_workspace.gmail.trusted.effects import create_draft


@clauz3.guarantee(mail.drafts_only())
@clauz3.guarantee(mail.only_recipients(["alice@example.com"]))
@clauz3.guarantee(mail.emails_at_most(1))
def main() -> None:
    create_draft("alice@example.com", "hello", "hello from clauz3")
```

Approval to create a draft does not authorize sending. Report success only
after execution succeeds; do not substitute delivery if drafting fails.

## Contracts

Recipient, count, uniqueness, and content contracts cover both draft and send
calls, including previews. They cover this library only.

| Contract | Scope |
|---|---|
| `drafts_only()` | Forbid all send calls, including previews; allow drafts |
| `no_emails()` | Same send-call restriction; drafts remain allowed |
| `no_drafts()` | Forbid draft creation; send calls remain allowed |
| `dry_run_only()` | Require explicit `dry_run=True` on every send call |
| `only_recipients(allowlist)` | Restrict recipient arguments to exact strings |
| `unique_recipients()` | At most one call per recipient string per run |
| `emails_at_most(count)` | Cap combined draft and send calls per run |
| `subject_must_contain(marker)` | Require a case-sensitive subject substring |
| `subject_length_at_most(n)` | Limit each subject's character count |
| `body_length_at_most(n)` | Limit each body's character count |
| `no_body_contains(text)` | Forbid a literal body substring |
| `no_subject_contains(text)` | Forbid a literal subject substring |
| `no_guarantees()` | Impose no email restriction |

Combine `dry_run_only()` with `no_drafts()` for previews without mailbox writes.
Combine `no_emails()` and `no_drafts()` to prohibit both effects. Defaults are
not recorded in effect facts: always pass `dry_run` explicitly when stating
`dry_run_only()`. Runtime defaults still prevent delivery if omitted.

Every public function has a docstring for tool discovery and generated skills.
Contract description templates supply the prose beside Python expressions in
approvals; expanded details show the scope docstring.

## Tests and maintenance

```bash
clauz3 test stdlib:google_workspace
# From the Clauz3 source checkout:
just -f src/clauz3/stdlib/google_workspace/tests/Justfile test
```

Proof fixtures are never executed. Runtime tests mock all subprocess calls:
they do not send mail or create real drafts. Signature regressions verify that
the selected library loads even when a program does not import email.
The repository's `just stdlib` runs this suite alongside the other libraries.

Edit `src/clauz3/stdlib/google_workspace/` and reinstall `stdlib:google_workspace` to refresh a
consumer project's copy. No separate repository needs to be synchronized.

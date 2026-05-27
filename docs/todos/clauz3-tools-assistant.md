# clauz3-tools-assistant — design TODO

A planning document for the next domain bud-off after
`clauz3-tools-autolabs` (see issue #41). This one wraps personal-assistant
CLIs — Google Workspace (`gog`), `pa-personal-assistant`, Resend — that
the user already uses.

This is a design doc, not a build plan. It exists to lock down the
testing-safety model before code is written, because the convention used
for `stdlib` and `autolabs` (real non-mock bodies, proof-only tests) is
**unsafe** for this domain.

## Why this domain is different

`clauz3-tools-autolabs` ships mock LIMS effects: `pipette` does nothing
real. `stdlib:filesystem` ships real effects but the side effects are
local-disk: bounded, idempotent-with-care, undoable in a temp dir.

The assistant domain is different on two axes:

1. **The side effects reach the outside world.** A real
   `send_email(to, subject, body)` body posts to Gmail / Resend. There is
   no undo. There is no test-temp-dir equivalent for someone's inbox.
2. **The CI/dev/prod split is collapsed.** `clauz3 test` against a real
   body either spams real accounts (catastrophic), or has to be muzzled
   somehow on every test path. The existing stdlib convention has no
   muzzle.

Without addressing this up front, the first PR that ships an
`assistant` tool risks sending real email from CI.

## Options for the safety model

### Option 1 — env-var dry-run gate

Body checks `CLAUZ3_ASSISTANT_DRY_RUN=1` (or similar) and refuses to
actually fire when set. CI sets it; production unsets it.

**Pros:** simplest. Existing stdlib convention preserved.

**Cons:** the safety is one missing env var away from disaster. Anybody
running `clauz3 run` in a shell that doesn't happen to have the var
exported will fire real effects. A new contributor cloning the repo and
running `clauz3 test` (which should be safe) fires real effects unless
the test harness is careful to export it. The trust boundary is the
env var, not the type system or the prover.

### Option 2 — shell-out, safety at consumer level

Body shells out to `gog` / `pa`. Whether those tools have a dry-run mode
is the consumer's problem.

**Pros:** none, really. Just punts the problem.

**Cons:** same as Option 1, with an extra layer of indirection.

### Option 3 — dry-run-by-default in the type signature, contract-enforceable

The trusted function signature explicitly carries a `dry_run: bool = True`
parameter. The default is `True` (no-op). To actually fire an effect, the
agent must pass `dry_run=False` explicitly.

```python
@deal.pre(lambda to, subject, body, dry_run: "@" in to)
@deal.has("net", "send", "global", "import", "trusted")
@effect(...)  # fluent: SentTo[to] = True
def send_email(
    to: str, subject: str, body: str, *, dry_run: bool = True,
) -> None:
    """Send email via gog/Resend. Defaults to dry_run=True (no-op).

    When dry_run=True, the body logs the would-be call and returns; no
    network traffic occurs. When dry_run=False, the body actually sends.
    """
    if dry_run:
        return
    # ... real send via gog / Resend
```

A `dry_run_only()` contract becomes available:

```python
@contract
def dry_run_only() -> ContractSpec:
    return SendEmail.all(lambda e: e.dry_run == True)
```

Production use requires **two intentional acts**:

1. Don't state `dry_run_only()` as a guarantee.
2. Pass `dry_run=False` at every call site.

Neither is the default. CI / `clauz3 test` doesn't need an env var to be
safe — the *default-True* parameter is the safety net.

**Pros:** safety is in the source, not the environment. The prover can
discharge `dry_run_only()`, so an approval policy can require it for any
agent that doesn't have explicit send authorization. Honest:
"dry run" is encoded once in the API and visible to every reader.

**Cons:** every call site has to explicitly pass `dry_run=False`. That's
a feature, not a bug, but it's a real ergonomic cost.

### Option 4 — non-stdlib-style: mock bodies, override at deploy

Ship the trusted layer with the mock-stub convention (bodies are
`pass`-only stubs, like `examples/email/`'s `send_email`). A production
consumer overrides the trusted module locally with a real implementation
they trust.

**Pros:** safest by construction — there is no real-body path in the
shipped repo.

**Cons:** loses "install and go." Every consumer has to wire their own
real impl, replicating gog/Resend integration each time. The "trusted
layer" becomes a contract-only artifact, not a runnable tool.

## Recommendation: Option 3

Option 3 is the cleanest. The default-dry-run pattern moves the safety
from the environment to the function signature, which is the right
trust boundary. The contract surface includes `dry_run_only()` so policy
can demand it, and the prover can discharge or reject any program based
on whether each call site passes `dry_run=False`.

Option 4 is the fallback if `dry_run` turns out to be awkward across
many subdomains (calendar invites, task creation, doc edits) — but I
expect the parameter pattern to generalise cleanly. Calendar:
`create_event(..., dry_run: bool = True)`. Tasks:
`add_task(..., dry_run: bool = True)`. Same shape.

### Validation: explicit passing is required, defaults fail closed

The mechanism has been validated against the current prover (see
`tests/test_dry_run_kwarg.py`). Three behaviours hold:

1. `send("a@x", dry_run=True)` — proves against `dry_run_only()`.
2. `send("a@x", dry_run=False)` — correctly fails the guarantee.
3. `send("a@x")` (relying on the default) — fails closed: the prover
   reports "unsupported guarantee expression (unknown effect field
   dry_run)" and exits non-zero, because the recorded effect fact does
   *not* reflect default kwarg values, only explicitly-passed ones.

The third behaviour is load-bearing and *strengthens* the design. The
runtime default `dry_run=True` is one safety layer (an accidental
`send("a@x")` is a no-op at runtime). The contract demanding an
explicit kwarg is the *second* layer: an agent cannot earn approval
without intentionally writing `dry_run=True` at the call site. Belt and
suspenders, with no overlap or redundancy.

This means agent code in the assistant domain should *always* pass
`dry_run` explicitly — the default is purely a runtime backstop against
typoed or unfinished call sites, not the discharge path.

## v1 sub-domain scope

Mirror `clauz3-tools-autolabs` (one repo, multiple sub-domains under
`tools/<service>/`). Initial domains, in priority order:

1. `tools/email/` — `send_email`, `read_email`. Highest stakes, most
   familiar shape, most-used `pa` surface. Ship first.
2. `tools/calendar/` — `create_event`, `list_events`. Second most likely
   to be agent-driven. Fluents help here: `event_already_exists` /
   `slot_conflicts`.
3. `tools/tasks/` — `add_task`, `complete_task`. Cheapest to ship.
4. `tools/docs/` — `read_doc`, `edit_doc`. Higher complexity, defer to
   v2.

Resend (outbound email-as-a-service) is a `tools/email/` backend choice,
not a separate sub-domain. The trusted layer's body can pick `gog` /
`pa` / Resend / SMTP via an env var, parallel to how `web_search` is
backend-configurable.

## Binary-vs-library shell-out

The user's `gog` and `pa` are CLI tools, not Python libraries. Two real
options:

- **Subprocess shell-out** to `gog` / `pa` binaries. Trusted body
  invokes `subprocess.run(["gog", "gmail", "send", ...])`. Requires the
  binary on PATH at `clauz3 run` time. No new Python deps. Resilient to
  upstream changes (CLI is a stable boundary).
- **Library import.** Treat `pa` as a Python library (it lives at
  `/Users/cjm/repos/stuff` per the skill registry). Cleaner integration,
  but couples the trusted layer to a specific package layout and pulls
  it as a dependency.

**Recommendation: subprocess shell-out.** The CLI is the stable
contract; the Python library is an implementation detail. Production
consumers can override the body if they want library-mode.

A note on `deal lint`: subprocess calls will require additional markers
(`stdout`, `stderr`, `network`, `global`, `import` — possibly more
depending on what deal infers). This is fine — the markers are honest
documentation of what the body really does.

## Concrete API sketch (Option 3 applied)

```python
# tools/email/trusted/effects.py
import deal
from clauz3.fluent import effect, fluent

SentTo = fluent("sent_to", key=str, value=bool, initial=False)


@deal.pre(lambda to, subject, body, dry_run: "@" in to)
@deal.has("net", "send", "global", "import", "trusted")
@effect(lambda to, subject, body, dry_run: SentTo.set(to, True))
def send_email(
    to: str,
    subject: str,
    body: str,
    *,
    dry_run: bool = True,
) -> None:
    """Send email. Defaults to dry_run=True (no real send)."""
    if dry_run:
        return
    # subprocess.run(["pa", "email", "send", ...]) — real send
    ...


# tools/email/trusted/contracts.py
Email = effect("send_email")

@contract
def dry_run_only() -> ContractSpec:
    return Email.all(lambda e: e.dry_run == True)

@contract
def only_recipients(allowlist: list[str]) -> ContractSpec:
    return Email.all(lambda e: e.to in allowlist)

@contract
def unique_recipients() -> ContractSpec:
    return Email.distinct(lambda e: e.to)

@contract
def emails_at_most(count: int) -> ContractSpec:
    return Email.count() <= count

@contract
def subject_must_contain(marker: str) -> ContractSpec:
    """Common safety pattern: every subject must include '[DRAFT]' etc."""
    return Email.all(lambda e: marker in e.subject)

@contract
def no_body_contains(substring: str) -> ContractSpec:
    """Secrets / banned-content guard before any send."""
    return Email.all(lambda e: substring not in e.body)
```

## Open questions

1. ~~**`dry_run` keyword vs positional.**~~ **Resolved.** Keyword-only
   (`*, dry_run: bool = True`) works correctly with the prover: explicit
   passes are recorded as fact fields and discharged against
   `dry_run_only()`; default-call sites fail closed. Locked in by
   `tests/test_dry_run_kwarg.py`.
2. **`SentTo` fluent semantics.** Should `dry_run=True` calls still
   record the fluent transition? Probably yes (the *intent* is recorded
   even when the side effect is skipped) — so contracts like
   `all_recipients_eventually_unsubscribed_at_end` work in test runs.
   But this means the fluent's final state isn't ground truth for what
   real emails were sent. Reframe semantics: the fluent records
   *attempted* sends, not actual ones.
3. **Markers for the dry-run body.** When `dry_run=True`, the body is a
   `return`, so `deal lint` should infer zero side effects. When
   `dry_run=False` it shells out. Declaring `@deal.has("net", "send",
   ...)` is honest about the worst case. The prover treats the call as
   the worst case anyway.

## Sequencing

1. ✅ File a tracking issue (#53; mirrors #41 for autolabs).
2. ✅ Validate keyword-arg handling in trusted facts (open question 1
   above). Locked in by `tests/test_dry_run_kwarg.py`.
3. Create `normalform-ai/clauz3-tools-assistant` (probably private; the
   email body's defaults are a feature, but the contents of the
   trusted layer may still be vendor-coupled in a way that's better
   kept internal until v1 stabilises).
4. Ship `tools/email/` first with the Option-3 safety model, full test
   suite (proof-only — no subprocess invocation in CI), README, and the
   `dry_run_only()` contract documented as the policy-admin's default.
5. Add `tools/calendar/`, `tools/tasks/` once email is in use.

## Related

- #41 — domain bud-off (autolabs precedent).
- #45 — fluents (the `SentTo` per-recipient fluent uses these).
- #47 — versioned cross-repo install (relevant when the assistant repo
  ships v1 and needs sha pinning + attestation; the audit story is
  particularly important for an effect that can actually send email).
- The user's `pa-personal-assistant` and `gog` skills define the real
  CLI surface this wraps.

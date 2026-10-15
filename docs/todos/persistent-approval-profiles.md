# Persistent approval profiles for ephemeral sessions

Status: design proposal, 2026-10-12. The first MVP is tracked in
[issue #290](https://github.com/cmungall/agent-deal/issues/290).
It implements exact contract preferences, persistent history, and profile
curation for ordinary `start` sessions; broader policy composition and analysis
below remain proposed. See the [current behavior and limitations](../how-to/approval-service.md#remember-decisions-across-sessions).
This note is intentionally outside the MkDocs navigation.

## Goal

Let a user build an explicit approval profile while doing ordinary work:

- approve or reject one request;
- remember that decision for future requests with the same proven contract
  and selected context;
- promote historical decisions into rules, or remove their contributions;
- inspect, disable, and eventually edit those rules in the approval UI;
- check the rules themselves before activating a profile revision.

The approval service can remain an ephemeral child of `clauz3 start`. Its
process lifetime is independent of the lifetime of the user's preferences.
Starting another session should recover the selected profile and its history,
without recovering old service credentials or reusing old execution receipts.

## Existing foundations

The launcher already establishes the working directory, selected trusted
libraries, harness configuration, and fresh approval-service credentials. It
starts and stops the service with the agent. Non-secret resume records already
persist under the user-wide clauz3 store.

[Approval policies](approval-policies.md) already support operator-authored
JSON rules. The service can conjoin contract clauses onto a program and
re-prove it before making an automatic decision. Policies are currently loaded
at startup; request history lives in the service process. The
[approval-dialog proposal](user-approval-dialog.md) already anticipates
`approved_remember`.

The [interactive grep replay](../development/2026-10-11-start-grep-replay.md)
provides a useful motivating case. Claude submitted a search, a diagnostic
listing after a binary-file error, and a revised search excluding bytecode.
All three programs proved and needed browser approval. The two search programs
had the same displayed guarantees; the listing had a different contract.
Remembering the first search contract could cover the revised search without
implying permission for every other read operation.

## Storage and lifecycle

Reuse the existing user-wide storage convention:

1. `CLAUZ3_HOME`, when explicitly configured by the operator;
2. otherwise `$XDG_DATA_HOME/clauz3`;
3. otherwise `~/.local/share/clauz3`.

Someone preferring `~/.local/clauz3` can select it with `CLAUZ3_HOME`. Avoid
introducing a second, competing home-directory convention for profiles.

An illustrative layout is:

```text
clauz3/
  settings.json             # selected profiles and operator preferences
  profiles/
    default.json            # editable, versioned profile document
  approval-history.sqlite   # requests, decisions, revisions, rule provenance
  sessions/                 # existing non-secret resume metadata
  ...                       # existing user-wide library store
```

Names and file formats are provisional. The requirements are durable storage,
transactional updates, versioned schemas, and safe concurrent use by multiple
ephemeral services. Do not put signing keys, bearer credentials, private login
URLs, or reusable receipts in these profile files.

The proposed startup flow is:

1. Resolve the operator-controlled store and selected profile.
2. Validate the profile and capture its revision and applicable project scope.
3. Establish session context and the selected trusted-library identities.
4. Start a fresh service with fresh credentials and access to the persistent
   profile/history store.
5. Launch the agent and show the active profile and project in the UI.
6. Record decisions durably as the session runs.
7. Stop the service when the agent exits; keep preferences and history.

An invalid selected profile should produce an explicit startup error, not
silently become an empty policy. A rejected live edit leaves the last valid
revision active and displays the validation failure. Profile persistence does
not require keeping a server listening between sessions.

Keep the distinction between operator-supplied mandatory policies and personal
preferences. A remembered permission cannot override a mandatory prohibition.
The existing `--policy` input needs an explicit place in this composition;
it must not silently be replaced by the default personal profile.

## Context: mostly fixed in a local session, explicit in every decision

For the current local launcher, the user and much of the context are fixed
when the session starts. This simplifies the first implementation, but does
not justify omitting context from stored rules or decision records.

| Information | Authority | Lifetime or qualification |
| --- | --- | --- |
| User/profile owner | Local operator/session setup | Fixed for the local session; authentication needed for future shared services |
| Agent and delegation | Launcher | Records which harness acts for the user; model name alone is not authority |
| Project/workspace identity and root | Operator/launcher | Fixed binding for this session; another checkout needs explicit scope selection |
| Selected libraries and contract implementations | Trusted selection and verifier | Bound to content/version identities; changes invalidate reuse |
| Proved guarantees and coverage | Verifier | Recomputed for each submitted program |
| Environment, such as local development or production | Operator configuration | Never inferred from agent prose or an arbitrary repository label |
| Time, expiry, revocation, profile revision | Service and persistent store | Can change during a session |
| Filesystem contents and external service state | Environment/trusted effects | Not frozen merely because the session context was captured |

Use stable identity/resource attributes for persistent facts and request
context for facts particular to this execution. Record the source of each
authority-bearing attribute. If a required attribute is missing or cannot be
validated, the request must not qualify for automatic approval.

Initially, remembered rules should default to the current project and user.
Binding a canonical local project root is a conservative first scope; a Git
remote URL or agent-supplied project name alone is insufficient. Moving a
project, sharing a profile across worktrees, or broadening it to all projects
requires an explicit scope change. Relative paths such as `.` are interpreted
against the bound project root.

Do not include every context field in an equality hash: a new session ID or
timestamp would prevent any useful reuse. A rule explicitly selects which
context attributes it constrains. The history records the full relevant
decision context, including fields not constrained by that rule.

## What "same signature" means

Use "same proven contract" in the UI. Internally, distinguish the identity of
the permission request from the identity of the submitted program and from a
cryptographic receipt signature.

A versioned contract fingerprint should cover:

- resolved trusted contract identities and canonical literal arguments;
- the complete guarantee bundle and coverage information, including explicit
  unconstrained domains;
- the authorized effect/library selection and relevant implementation and
  dependency identities;
- the version of the representation and verification semantics used to
  interpret that bundle.

Matching additionally requires the rule's context constraints. Import aliases,
formatting, and decorator ordering should not distinguish equivalent canonical
bundles. The first version need not decide arbitrary logical equivalence.
Fingerprint construction must use verified, resolved data, not trust the
submitter's displayed strings or claimed proof flags.

Program source is allowed to change under an exact contract rule. Every new
program must still pass the normal proof and authorization checks and receive
a fresh receipt bound to that program and request. An earlier receipt is never
the reusable permission. The existing consent digest includes the program
hash, so it is not a drop-in remembered-contract key.

Showing the full consent scope before saving matters: a read constraint for
one effect is not a universal no-writes guarantee, and a count bound per run
does not limit the number of future runs. No automatic generalization should
remove coverage, change literal arguments, or expand the trusted selection.

## User interface

Each pending card offers:

- **Approve once**
- **Approve and remember this contract**
- **Reject once**
- **Reject and remember this contract**

The remembered action includes a compact scope preview: profile, user,
project, contract summary, relevant trusted version, and any expiry. Saving
the current decision and its new rule should be one durable transaction.
Ordinary approvals do not automatically become training examples or rules.

Add a **Profile** view alongside pending and historical requests. Show:

- active profile and revision;
- each rule's decision, scope, enabled state, and explanation;
- originating historical cards and subsequent requests it matched;
- validation findings and any stale library/context bindings;
- controls to inspect, disable, remove, and later edit the rule.

Historical cards offer **Add to profile**, **View rule**, and **Remove from
profile**. Preserve the original decision as an audit event. Changing the
profile cannot retroactively turn a past rejection into an approval or execute
an old request.

Represent the link from a historical decision to a rule explicitly. Multiple
cards may support the same rule. Removing one card's contribution need not
delete a rule supported by other cards or explicitly authored by the user;
show the remaining sources and offer to disable the rule itself.

Removing a rule is different from insisting on a prompt: another rule might
still grant permission. Provide an explicit **Always ask for this contract**
exception. Automatic decisions remain visible in history, with the rule,
profile revision, context, and checked evidence that justified them.

## Decision semantics and generalization

Proposed precedence, after proof/selection validation:

1. Applicable mandatory or remembered rejection: reject.
2. Applicable always-ask exception: ask.
3. Applicable remembered or explicitly authored permission: approve.
4. No applicable rule: ask.

Errors and unknown results are separate from a rule that does not match.
An unresolved potentially applicable prohibition must block automatic
approval; a successful allow rule cannot hide that failure. Report the error
and permit operator diagnosis. Manual consent cannot waive a failed required
program proof or unauthorized trusted selection.

Exact remembered rejection is deliberately different from a general safety
prohibition. Rejecting a contract allowing ten emails must not automatically
reject a stronger contract permitting one. "Remember this rejection" must
not mechanically convert the rejected clauses into the existing
`unless_proven` form, which has different semantics.

Later, a separate **Broaden this rule** action can change a saved exact match
into a logical requirement. For example, allow any search pattern provided
all reads stay in this project and all relevant write effects are excluded.
The existing conjoin-and-prove mechanism can check those requirements against
each new program. Show the changed scope and historical impact before
activating that broader grant.

Distinguish approval status from execution outcome. A program approved under a
valid contract may still encounter a runtime decoding error. Record that
failure without silently learning a new rejection preference from it.

## Editing and checking the profile

The UI and direct file editing should use the same versioned representation
and validation pipeline. Initial JSON editing is sufficient; a text policy
language can come later. Show a diff and the new scope before activating a
broader grant.

Three levels of checking are useful:

1. **Structural validation:** schema, types, supported operators, resolved
   trusted contract references, scope validity, expiry, and version bindings.
2. **Rule analysis:** conflicting decisions, dead or redundant rules,
   subsumption, and permission expansion relative to a previous revision.
3. **Profile guarantees:** prove requirements such as "every automatically
   approved program must satisfy the configured no-write contracts" within
   the supported policy/contract fragment.

Return counterexamples where available and explicit unknown results elsewhere.
Do not present the existing ability to prove a concrete program as an already
implemented proof of a property of every possible profile decision.

Replay proposed revisions against retained history and operator-authored
examples. Report which requests would now approve, reject, or ask, and why.
This is an impact preview and regression check; it is not a proof about all
future requests. Incompatible historical library versions remain explicitly
uncheckable unless the required trusted artifacts are available and authorized.

## Revisions, concurrency, and enforcement boundaries

Multiple sessions may share a profile. Use revision-checked writes so two UI
edits cannot silently overwrite one another. File edits need the same atomic
validation/activation path; watching a partially written file must not activate
an incomplete policy. Retain revision history and support undo.

Evaluate each decision against one coherent profile/context snapshot. Before
issuing a receipt, ensure the relevant revision and context have not changed;
otherwise re-evaluate. Surface profile changes in running services. A changed
profile may be applied to pending requests through an explicit re-evaluation
action; an idle card should not unexpectedly begin execution because another
session edited a rule.

Rule removal affects future decisions. It does not undo executed effects or
automatically revoke an already issued receipt. If immediate revocation before
execution becomes a requirement, add an execution-time revision/revocation
check; do not claim an offline signed receipt already provides that behavior.
Expiry and other mutable context predicates likewise need a defined enforcement
point and receipt binding, not merely an audit entry.

The profile and history store belongs to the operator and should be outside
agent-writable project files, with restrictive local permissions. That location
alone is not a security boundary: approved filesystem effects could otherwise
reach it. Profile mutations must use an operator-authenticated administrative
path, and the implementation must prevent ordinary agent-run effects from
rewriting the authority store. The current same-user harness confinement is not
an OS sandbox; unrestricted/debug sessions must not silently inherit assumptions
that depend on restricted execution.

History can contain sensitive source, paths, and output. Define retention and
deletion controls, keep credentials out of records, and show what is persisted.
Persist structured contracts and enough source/proof metadata to support the
promised replay features; disclose when deletion makes a historical replay
unavailable. Do not resurrect orphaned pending requests for execution after a
service restart. A new session submits and checks a new request.

## Precedents and a future rule language

[Cedar](../explanation/cedar.md) supplies a useful request model: principal,
action, resource, and context. In this design the action can be execution of a
proved program; the policy evaluates independently established proof evidence
alongside the user's context. Cedar does not replace the program prover.

[Cedar policy templates](https://docs.cedarpolicy.com/policies/templates.html)
are a concrete precedent for a UI action creating a durable permission. Its
[context guidance](https://docs.cedarpolicy.com/bestpractices/bp-using-the-context.html)
distinguishes stable entity attributes from request-specific data and describes
agents acting on behalf of users. Template changes can affect all linked
policies, which reinforces the need for revision and impact previews here.

[OPA's Rego](https://www.openpolicyagent.org/docs/policy-language) is a
Datalog-inspired precedent for the desired declarative syntax.
[XACML 3.0](https://docs.oasis-open.org/xacml/3.0/xacml-3.0-core-spec-os-en.html)
is a standards precedent for separating attribute provision, policy decision,
and enforcement, and for distinguishing non-applicability from indeterminacy.
[IAM Access Analyzer](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-analyzer-checks-validating-policies.html)
provides a precedent for checking whether policy edits introduce new access.

The [background discussion](../explanation/background.md#mechanics-worth-borrowing)
already proposes a Datalog/Prolog-like frontend. A future frontend should be
declarative, schema-checked, bounded in evaluation, and explicit about unknown
facts and negation. It must not execute arbitrary Python or accept agent
assertions as verified facts. Browser-created and handwritten rules should
compile to the same policy representation and use the same checker.

Cross-request budgets, temporal rules, and prerequisites require a trustworthy
event ledger and concurrency semantics. Persisting cards alone does not enforce
"at most ten emails per day": decisions may race, and approved programs may
fail after partial execution. Treat accounting/reservation and temporal
enforcement as a later feature with its own model.

## Delivery sequence and acceptance criteria

1. **Persistent history and profile storage:** fresh services recover history
   and selected settings; credentials remain ephemeral; concurrent sessions
   do not lose updates.
2. **Exact remembered decisions and Profile UI:** rules survive restart, remain
   scoped to the selected user/project/trusted versions, and have visible
   provenance. Different code with the same verified contract can match;
   another project or changed trusted code cannot silently match.
3. **History curation, direct editing, and impact previews:** add/remove
   contributions, disable rules, always-ask exceptions, validation, revisions,
   and undo. Invalid edits never activate.
4. **Generalized rules and policy analysis:** entailment-based permissions,
   permission-expansion checks, profile guarantees, and an optional declarative
   frontend over the same internal representation.

Exercise the complete `clauz3 start` workflow in acceptance tests. In particular,
approve-and-remember a search, exit, restart with a fresh service, and confirm
that an independently proved matching search receives a fresh automatic
approval. Verify that rejection, removal, changed context, unknown evaluation,
and conflicting concurrent edits produce the documented outcomes. Retain the
API-level tests as diagnostics alongside this user-workflow check.

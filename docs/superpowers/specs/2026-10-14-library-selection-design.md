# Library selection in the workspace

Status: approved 2026-10-14.

## Problem

The workspace UI mixes three library states and two scopes without saying
which is which. A library can be not installed, installed, or installed and
selected. "Selected" means two things: the saved default for new
conversations, and the set a running conversation actually uses. The sidebar's
"SELECTED LIBRARIES" shows the second while the Libraries page edits the first.
"+ New conversation" silently uses the saved default. A conversation's set
cannot change once started, and a conversation whose libraries changed on disk
can never continue.

## Model

| Scope | Lives in | Edited from |
|---|---|---|
| Installed | `.clauz3/tools/` | Libraries page (install, update, remove) |
| Default for new conversations | `libraries.json` per project | Libraries page toggle |
| This conversation | the conversation's saved state, with a fingerprint of the files | Message-box library menu |

## Libraries page

One list of library cards, modelled on `chrome://extensions`, with search and
All / Installed filters instead of Installed / Browse tabs. An installed card
has a "Use in new conversations" toggle that saves immediately (dependencies
are added for you), an "In use" tag when the current conversation uses it, and
Details, Review update and Remove actions. An uninstalled card is dimmed and
has an Install action. Install, update and remove keep today's review dialog.
"Save selection" and "Apply & start new conversation" go away.

## Message box

The current conversation's libraries appear as chips above the prompt, with a
"＋ Libraries" button that opens a menu of installed libraries with toggles,
Apply and Cancel, and a "Manage libraries" link. The sidebar's library list is
removed.

Toggles collect into one pending change. Apply sends the whole set; the server
adds dependencies, restarts the agent once and resumes the same agent session
with a bridge built for the new set.

## Changing libraries in a started conversation

A change is *narrowing* when the new set is a subset of the old one and the
old set's files still match the conversation's fingerprint. Anything else
(an added library, or changed files) is *widening*.

- Before the first message, any change applies at once with no side effects.
- Narrowing applies at once and adds a transcript marker.
- Widening needs confirmation. The server answers the first request with
  `clauz3/confirm_libraries`; the menu then explains that earlier messages may
  carry text from files or pages the agent read, and offers "Add and continue"
  or "Start a new conversation with these libraries" (a `clauz3/new` with
  explicit domains, which does not change the saved default).
- A confirmed widening pauses automatic approvals for the rest of the
  conversation. The pause is saved with the conversation, so reopening it does
  not lift it.

Every applied change in a started conversation publishes a
`clauz3/libraries_changed` transcript entry (added, removed, updated, whether
approvals are paused) and adds to a list of changes that is prepended, as one
note, to the next prompt sent to the agent, telling it what changed since its
last prompt and to call `tools` again. The note is not shown as user text.

## Paused automatic approvals

Automatic approvals come from remembered profile rules or, with
`clauz3 ui --policy`, from a policy file. Remembered rules are project-wide and
their signatures already bind the exact trusted roots and file contents.
Pausing addresses a different risk: a conversation whose context may hold
injected instructions gains new capabilities, and a matching allow rule or
policy would then approve without a human.

While the active conversation is paused, a remembered allow rule or a policy
`auto_approved` decision is ignored and the request waits for a manual
decision; the request records `approvals_paused` and its review card says why.
Remembered denials and policy `auto_rejected` decisions still apply. The
workspace passes the pause to the approval service together with the
conversation's trusted roots, the same way it already activates them.

## Reopening a conversation whose libraries changed

Today this opens read-only with "This conversation's libraries changed". The
notice gains a "Continue with current libraries" button that sends the
conversation's library list through the widening flow. Libraries that are no
longer installed are dropped and reported as removed; updated ones count as
added.

## Testing

- pytest: narrowing vs widening classification, confirmation round trip,
  pause persisted across reopen, allow rules ignored and deny rules applied
  while paused, the agent note prepended once, continue-after-change.
- Browser check: chips, menu apply, confirmation, marker and paused pill,
  Libraries page toggle and filters.

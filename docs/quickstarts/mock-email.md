# Approve a mock email

See the approval workflow without sending an email: ask Claude to use the
repository's mock sender, inspect the guarantees, and approve the request.
Proofs, approval decisions and execution receipts are real; the sender's
implementation does nothing.

## Install once

--8<-- "docs/includes/quickstart-install.md"

## Open the email example

From the checkout root:

```bash
cd examples/email
clauz3 ui claude
```

ClauZ3 opens a local browser workspace with chat and approvals together. This
example already contains its trusted email library. Under **Libraries**, its
mock effects are labelled **Simulated**. The label records the library
author's declaration; it does not enforce isolation. You can inspect the
implementation in `tools/email/trusted/effects.py`.

The agent can discover selected tools and submit programs for proof and
approval. It cannot directly send email or install another trusted library.

## Ask for an email

Paste this into chat:

> Use the installed email library to send bob@example.com the message
> "Hello from ClauZ3". Guarantee that only Bob can be emailed, there is at
> most one email, and recipients are unique. Submit it for approval.

The agent writes a small program. Watch its run progress through proof to
**Pending user decision**. An approval card appears in the conversation.

Check for these guarantees (the agent's import alias may differ):

- `emails.only(['bob@example.com'])`
- `emails.at_most(1)`
- `emails.unique_recipients()`

Expand **Show source** or **Proofs** if you want to inspect the details. If
one of the requested guarantees is missing, choose **Need more info** and
ask the agent to include it before approving.

![An approval card with a declared-mock notice, guarantees and decision controls](../assets/mock-declarations.png)

*An example card. This one states only the recipient guarantee, so it also
flags the missing recommended `unique_recipients` guarantee. Your card should
include all three guarantees requested above.*

## Approve and see the result

Click **Approve**. The runner verifies the signed approval and calls the mock
sender. The agent should report completion. No email arrives: the implementation
is a no-op. There is currently no simulated outbox to inspect.

To try the other path, request another mock email and click **Reject**. The
program is not executed, and the decision is returned to the agent.

**Approve and remember** saves a decision for matching future contracts in
this project with the same trusted code. It does not approve every future
email request. The **Approval profile & history** link lets you inspect and
remove remembered rules. See [approval profiles](../how-to/approval-service.md) for matching
and storage details.

## Stop or continue

**Stop** ends the active agent and workers. Press Ctrl-C in the terminal that
launched `clauz3 ui` to stop the service. Chat history survives service restarts.

Next, try [real file work](real-files.md), where approval produces a file you
can inspect. For how proofs relate to trusted implementations, read
[Concepts](../explanation/concepts.md) and
[mock declarations](../reference/effect-specs.md#declaring-mock-implementations).
An enforced demo mode with a simulated outbox is tracked separately in
[issue #320](https://github.com/cmungall/agent-deal/issues/320).

## Prefer a terminal agent?

From the same `examples/email` folder, run this instead of `clauz3 ui`:

```bash
clauz3 start claude
```

Enter the same prompt in Claude's terminal. Approvals appear in the browser
opened by ClauZ3. Exiting Claude stops its approval service. The printed approver
URL grants access to approval decisions; keep it private.

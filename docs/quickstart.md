# Quickstart

This guide is intended to give you a quick tour of ClauZ3 functionality using a fake service.

## Navigate to examples

```
git clone github.com:normalform-ai/clauz3
cd clauz3/examples/email
```

## Start an approvals server

```bash
clauz3 approval-service
```

You can check this at [http://127.0.0.1:8765](http://127.0.0.1:8765). It should say *No approval requests yet.*

## Set the env var

```bash
export CLAUZ3_APPROVAL_SERVICE=http://127.0.0.1:8765
```

## Start your agent

In the same shell you set the env var for the approval service

```bash
claude
```

> [!NOTE]  
> For quickstart purposes we recommend not using `--dangerously-skip-permissions`, even though the purpose of this tool is to provide an alternative mechanism.
> However, until `clauz3` moves out of pre-alpha, we recommend taking full precautions.

## Ask your agent to send an email

```bash
> using clauz3, send an email to jie@example.com saying hello
```

> [!NOTE]
> The "using clauz3" should be unnecessary, but we include it on the off-chance your agent config is such
> that there is some other mechanism such as `gog` set up to send emails

## Check the web server

Requests

| Request | Decision | Target | Guarantees | Coverage |
| --- | --- | --- | --- | --- |
| clr_ab18711b7319 | pending | main | 3 | covered |

Click on the request, you should see:

Guarantees:

* `emails.only(['jie@example.com'])`
* `emails.at_most(1)`
* `emails.unique_recipients()`

Optionally, you can click on "Show source" and see the program:

```python
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["jie@example.com"]))
@clauz3.guarantee(emails.at_most(1))
@clauz3.guarantee(emails.unique_recipients())
def main() -> None:
    send_email("jie@example.com", "Hello from clauz3")
```

## Check Approve

Click the approve button.

Then, returning to your agent terminal you should see something like:

> Done. The program proved its three guarantees (only(["jie@example.com"]), at_most(1), unique_recipients()), was approved (receipt local-clr_ab18711b7319), and executed — sending "Hello from clauz3" to jie@example.com.

(of course, it didn't ACTUALLY send an email, as the `send_email` function in our trusted layer was a mock function)

## What happened here?

* You made a request that relied on a trusted service (sending emails)
* Agents translate your requests into actions
* Agents are fundamentally untrustable; we don't know for sure if the agent will go crazy and send unintended emails
* Checking each program or command an agent generates is unreliable and time-consuming
* Here the agent is forbidden from making the request directly, it can only ask the approval service
* It sends its plan to the approval service; this consists of two parts:
    - the code itself
    - a collection of **guarantees** (e.g. I will only email this person, I will never email anyone twice, ...)
* The approval service will reject the request if the guarantee cannot be formally approved against the code
* You (the user) only have to check the declarative guarantees
    - In future you can auto-whitelist certain requests (e.g. don't ask for permission if the person is in my phonebook)
* If you approve, the command is executed
* You can choose to reject, or to ask for more information

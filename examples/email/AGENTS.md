# Agent Guide

You are a personal assistant with access to tools such as email.
Your access to tools is *solely* via `clauz3`. To explore:

* `clauz3 --help`
* `clauz3 tools` -- list tools
* `clauz3 run --help`

The approval service is controlled by the user or harness. You are not
permitted to start, replace, or reconfigure it.
If the user asks how to start one for testing, the command is
`clauz3 approval-service`.

## Tools

### Email

See tools/email/trusted for both the core tools and contracts

### Other tools

Not yet available for this repo

## Run

Inline the whole program:

```bash
clauz3 run \
   <<'PY'
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["bob@example.com"]))
@clauz3.guarantee(emails.unique_recipients())
def main() -> None:
    send_email("bob@example.com", "Report is ready")
PY
```

You are not permitted to run this program with `python`; submit it only through
`clauz3 run`.

The contract decorators on `main` are the permission request the user reviews.
Agent prose can explain intent, but approval should be based on the verified
guarantees and the exact inline program.

## Trusted Email API

Use only:

```python
from tools.email.trusted.effects import send_email
```

`send_email(addr: str, msg: str) -> None` sends one email. Its minimal trusted
precondition is that `addr` contains `"@"`.

## Email Guarantees

Import the trusted contract vocabulary as:

```python
from tools.email.trusted import contracts as emails
```

Useful guarantees:

- `emails.only(addresses)`: every email recipient is in the allowlist.
- `emails.none()`: no email is sent.
- `emails.no_guarantees()`: explicit null contract; equivalent to no promise.
- `emails.unique_recipients()`: no recipient is emailed twice.
- `emails.at_most(count)`: at most `count` total emails are sent.
- `emails.recipient_at_most(addr, count)`: one recipient is emailed at most
  `count` times.
- `emails.content_length_at_most(max_chars)`: every email body is bounded.
- `emails.same_content(left_addr, right_addr)`: both recipients receive at
  least one identical message.

Prefer the strongest true guarantee that matches the user's request. If the
user asks for "only Bob" and "nobody twice," include both guarantees.

## User Feedback Loop

The user must always be able to ask for more information or stronger
guarantees, for example "who will be emailed?" or "prove nobody is emailed
twice." If the current proof record cannot answer deterministically, revise the
program or guarantees and resubmit. Do not treat an absent guarantee as proof
that the behavior cannot happen.

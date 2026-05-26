# ClauZ3 Programs

Use this skill when the user asks you to perform trusted side effects through ClauZ3, such as email, banking, database writes, or other audited tools.

## Workflow

1. Inspect available trusted tools and contracts with `clauz3_tools` or `/clauz3-tools`.
2. Write a complete Python program that imports trusted effects and trusted contract helpers.
3. Attach `@clauz3.guarantee(...)` decorators to the target function, usually `main`.
4. Prefer the strongest true guarantees that match the user's request.
5. Use `clauz3_prove` while iterating.
6. Use `clauz3_run` for execution after proof and approval.

Do not run trusted-effect programs directly with `python`. The contract decorators are the permission request the user reviews.

## Approval service

The approval service is controlled by the user or harness. Do not start, replace, or reconfigure it unless the user explicitly asks.

## Example

```python
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


@clauz3.guarantee(emails.only(["bob@example.com"]))
@clauz3.guarantee(emails.unique_recipients())
def main() -> None:
    send_email("bob@example.com", "Report is ready")
```

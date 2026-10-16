# ruff: noqa: F821
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email

import clauz3


# `while` is outside the proved subset: neither the prover nor the
# interpreter registers a handler for `astroid.While`, so this program skips
# and `clauz3 run` refuses it before asking for approval. The loop is bounded
# and the guarantee holds; the skip is about the construct, not the content.
@clauz3.guarantee(emails.only(["bob@example.com"]))
def main() -> None:
    n = 0
    while n < 2:
        send_email("bob@example.com", "hi")
        n = n + 1

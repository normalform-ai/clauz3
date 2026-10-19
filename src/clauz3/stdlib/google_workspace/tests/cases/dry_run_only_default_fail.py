# ruff: noqa: F821
from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import send_email

import clauz3


# Relies on the runtime default dry_run=True but does NOT pass it
# explicitly. The contract `dry_run_only()` queries `e.dry_run` which is
# not in the recorded fact (default kwargs aren't reflected), so the
# prover fails closed. This is the load-bearing safety property: agents
# can't earn approval without intentionally stating dry_run.
@clauz3.guarantee(email.dry_run_only())
def main() -> None:
    send_email("alice@example.com", "[DRAFT] hello", "hi")

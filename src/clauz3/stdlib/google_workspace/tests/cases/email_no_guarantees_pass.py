# ruff: noqa: F821
from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import send_email

import clauz3


# Explicit null contract: agent asks for no guarantees. The trusted
# preconditions still hold (addr has "@", subject non-empty), so the
# call is well-formed, but the contract surface makes no claims about
# behaviour. This is the "I know what I'm doing, document that I'm
# claiming nothing" pattern.
@clauz3.guarantee(email.no_guarantees())
def main() -> None:
    send_email("anyone@example.com", "anything", "anything")

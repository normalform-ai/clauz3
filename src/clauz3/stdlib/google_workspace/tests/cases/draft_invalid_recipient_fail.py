from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import create_draft

import clauz3


@clauz3.guarantee(email.drafts_only())
def main() -> None:
    create_draft("alice", "hello", "hi")

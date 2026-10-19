from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import create_draft

import clauz3


@clauz3.guarantee(email.no_drafts())
def main() -> None:
    create_draft("alice@example.com", "hello", "hi")

from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import create_draft

import clauz3


@clauz3.guarantee(email.emails_at_most(1))
def main() -> None:
    create_draft("alice@example.com", "hello", "hi")
    create_draft("bob@example.com", "hello", "hi")

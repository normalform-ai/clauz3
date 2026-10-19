from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import create_draft

import clauz3


@clauz3.guarantee(email.only_recipients(["alice@example.com"]))
def main() -> None:
    create_draft("bob@example.com", "hello", "hi")

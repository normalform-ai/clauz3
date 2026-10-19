from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import create_draft

import clauz3


@clauz3.guarantee(email.subject_must_contain("hello"))
def main() -> None:
    create_draft("alice@example.com", "bye", "hi")

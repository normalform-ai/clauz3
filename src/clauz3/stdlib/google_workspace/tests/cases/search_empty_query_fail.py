from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import search_messages

import clauz3


@clauz3.guarantee(email.read_only())
def main() -> None:
    search_messages("", 1)

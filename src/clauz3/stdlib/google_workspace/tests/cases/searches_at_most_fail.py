from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import search_messages

import clauz3


@clauz3.guarantee(email.searches_at_most(1))
def main() -> None:
    search_messages("from:alice@example.com", 1)
    search_messages("from:bob@example.com", 1)

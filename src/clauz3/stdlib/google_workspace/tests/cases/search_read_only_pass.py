from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import search_messages

import clauz3


@clauz3.guarantee(email.read_only())
@clauz3.guarantee(email.searches_at_most(1))
def main() -> None:
    for message in search_messages("from:alice@example.com", 1):
        print(message.date, message.sender, message.subject)
        print(message.body)

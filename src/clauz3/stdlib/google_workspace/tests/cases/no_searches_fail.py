from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import search_messages

import clauz3


@clauz3.guarantee(email.no_searches())
def main() -> None:
    search_messages("is:unread", 5)

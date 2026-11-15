from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import create_draft, search_messages

import clauz3


@clauz3.guarantee(email.drafts_only())
@clauz3.guarantee(email.searches_at_most(1))
@clauz3.guarantee(email.emails_at_most(10))
def main() -> None:
    for message in search_messages("is:unread from:alice@example.com", 10):
        create_draft("alice@example.com", "Re: " + message.subject, "Thanks!")

from tools.google_workspace.gmail.trusted import contracts as email
from tools.google_workspace.gmail.trusted.effects import create_draft

import clauz3


@clauz3.guarantee(email.drafts_only())
@clauz3.guarantee(email.only_recipients(["alice@example.com"]))
@clauz3.guarantee(email.emails_at_most(1))
@clauz3.guarantee(email.unique_recipients())
@clauz3.guarantee(email.subject_must_contain("hello"))
@clauz3.guarantee(email.subject_length_at_most(20))
@clauz3.guarantee(email.body_length_at_most(20))
@clauz3.guarantee(email.no_body_contains("secret"))
@clauz3.guarantee(email.no_subject_contains("secret"))
def main() -> None:
    create_draft("alice@example.com", "hello", "hello from clauz3")

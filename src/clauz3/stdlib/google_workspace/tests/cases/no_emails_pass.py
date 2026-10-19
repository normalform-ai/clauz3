# ruff: noqa: F821
from tools.google_workspace.gmail.trusted import contracts as email

import clauz3


@clauz3.guarantee(email.no_emails())
def main() -> None:
    pass

# ruff: noqa: F821
from tools.email.trusted import contracts as emails

import clauz3


@clauz3.guarantee(emails.none())
def main() -> None:
    pass

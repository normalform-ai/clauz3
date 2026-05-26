# ruff: noqa: F821
from tools.text.trusted import contracts as text
from tools.text.trusted.effects import send_message

import clauz3


@clauz3.guarantee(text.sends_at_most(2))
def main() -> None:
    send_message("general", "one")
    send_message("general", "two")
    send_message("general", "three")

# ruff: noqa: F821
from tools.text.trusted import contracts as text
from tools.text.trusted.effects import send_message

import clauz3


@clauz3.guarantee(text.no_guarantees())
def main() -> None:
    send_message("general", "anything goes here")

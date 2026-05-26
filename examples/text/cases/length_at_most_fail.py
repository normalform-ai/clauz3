# ruff: noqa: F821
from tools.text.trusted import contracts as text
from tools.text.trusted.effects import send_message

import clauz3


@clauz3.guarantee(text.length_at_most(20))
def main() -> None:
    send_message("general", "this message is clearly longer than twenty chars")

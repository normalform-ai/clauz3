# ruff: noqa: F821
from tools.text.trusted import contracts as text
from tools.text.trusted.effects import send_message

import clauz3


@clauz3.guarantee(text.length_between(3, 20))
def main(urgent: bool) -> None:
    if urgent:
        send_message("alerts", "page on-call now")
    else:
        send_message("general", "all quiet")

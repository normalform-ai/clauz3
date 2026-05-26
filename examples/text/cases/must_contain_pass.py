# ruff: noqa: F821
from tools.text.trusted import contracts as text
from tools.text.trusted.effects import send_message

import clauz3


@clauz3.guarantee(text.must_contain("[automated]"))
def main() -> None:
    send_message("general", "[automated] build succeeded")
    send_message("alerts", "nightly job done [automated]")

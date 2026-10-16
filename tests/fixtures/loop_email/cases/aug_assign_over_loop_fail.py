# ruff: noqa: F821
#
# agent-deal#139 review. `addr += "x"` rebinds an outer name inside a loop over
# query results. The quantified path evaluates the body once, so every
# iteration's call would be checked against the pre-loop `addr` and the
# precondition would discharge -- while at runtime the second row calls
# `send_email_only_to_bob("bob@example.comx", ...)`. The augmented spelling
# must be refused as loop-carried state, exactly like `addr = addr + "x"`.
from qtrusted import contracts
from qtrusted.effects import db_query, send_email_only_to_bob

import clauz3


@clauz3.guarantee(contracts.all_trivial())
def main() -> None:
    addr = "bob@example.com"
    for _row in db_query("users"):
        send_email_only_to_bob(addr, "hi")
        addr += "x"

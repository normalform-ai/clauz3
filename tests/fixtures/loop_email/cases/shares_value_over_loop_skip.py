# ruff: noqa
import clauz3
from qtrusted import contracts
from qtrusted.effects import db_query, send_email


@clauz3.guarantee(contracts.same_content_two_bobs())
def main() -> None:
    for row in db_query("users"):
        send_email(row.email, "hi")

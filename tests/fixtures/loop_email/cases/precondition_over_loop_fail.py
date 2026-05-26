# ruff: noqa
import clauz3
from qtrusted.effects import db_query, send_email_only_to_bob


@clauz3.guarantee(lambda: True)
def main() -> None:
    for row in db_query("users"):
        send_email_only_to_bob(row.email, "hi")

"""Tutorial sender: deliberately does nothing; no account or network needed."""

import clauz3


@clauz3.requires(lambda addr, msg: "@" in addr, message="addr must contain @")
@clauz3.trusted
@clauz3.mock
def send_email(addr: str, msg: str) -> None:
    """Simulate sending an email. Nothing is delivered or saved to an outbox."""
    pass

import deal


@deal.pre(lambda account, amount: amount >= 0, message="amount must be non-negative")
@deal.has("trusted")
def withdraw(account: str, amount: int) -> None:
    """MOCK trusted withdrawal.

    Contract: amount must be non-negative.
    """
    pass

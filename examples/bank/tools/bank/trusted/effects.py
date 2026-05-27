import deal


@deal.pre(lambda account, amount: amount >= 0, message="amount must be non-negative")
@deal.has("trusted")
def withdraw(account: str, amount: int) -> None:
    """MOCK trusted withdrawal.

    Contract: amount must be non-negative.
    """
    pass


@deal.post(lambda result: result >= 0, message="balance must be non-negative")
@deal.has("bank_read")
def balance(account: str) -> int:
    """MOCK trusted balance lookup (amount outstanding on an account).

    Contract: the returned balance is non-negative.
    """
    return 0

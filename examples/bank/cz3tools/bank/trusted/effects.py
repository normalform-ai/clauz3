import clauz3


@clauz3.requires(
    lambda account, amount: amount >= 0, message="amount must be non-negative"
)
@clauz3.trusted
@clauz3.mock
def withdraw(account: str, amount: int) -> None:
    """MOCK trusted withdrawal.

    Contract: amount must be non-negative.
    """
    pass


@clauz3.ensures(lambda result: result >= 0, message="balance must be non-negative")
@clauz3.trusted
@clauz3.mock
def balance(account: str) -> int:
    """MOCK trusted balance lookup (amount outstanding on an account).

    Contract: the returned balance is non-negative.
    """
    return 0

"""Bank predicates built from generic trusted effect facts."""

from clauz3.spec import ContractSpec, contract, effect

Withdraw = effect("withdraw")


@contract
def max_spend(limit: int) -> ContractSpec:
    """Guarantee that total withdrawals are at most limit."""
    return Withdraw.sum(lambda w: w.amount) <= limit


@contract
def only_account(account: str) -> ContractSpec:
    """Guarantee that every withdrawal targets this account."""
    return Withdraw.all(lambda w: w.account == account)

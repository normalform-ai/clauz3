from clauz3.spec import ContractSpec, contract, effect
from sigtrusted.effects import do_thing

Thing = effect(do_thing)


@contract
def calls_at_most(n: int) -> ContractSpec:
    """Guarantee ``do_thing`` is called at most ``n`` times."""
    return Thing.count() <= n

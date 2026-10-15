from clauz3.spec import ContractSpec, contract
from clauz3.spec import no_guarantees as core_no_guarantees


@contract
def no_guarantees() -> ContractSpec:
    """The case states no relation, so only the signature check can refuse it."""
    return core_no_guarantees()

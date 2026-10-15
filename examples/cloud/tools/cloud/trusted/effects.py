import clauz3
from clauz3.fluent import effect, fluent

# Successor-state fluent: a service is capped unless this program removes its
# cap. ``deploy`` always installs a cap; ``remove_spend_cap`` is the explicit
# opt-out ("I will be responsible for subsequent charges"). Contracts in
# ``contracts.py`` query the final valuation, so an agent may lift a cap
# mid-program only if a later deploy restores one.
Capped = fluent("capped", key=str, value=bool, initial=True)


@clauz3.ensures(lambda result: result >= 0, message="estimate must be non-negative")
@clauz3.trusted
def estimate_monthly_cost(service: str) -> int:
    """MOCK trusted cost estimate for ``service`` in whole USD per month.

    Contract: the returned estimate is non-negative. Nothing bounds it from
    above, so a cap computed from it must be clamped by the agent.
    """
    return 0


@clauz3.requires(
    lambda service, spend_cap_usd: spend_cap_usd > 0,
    message="spend_cap_usd must be positive",
)
@clauz3.trusted
@effect(lambda service, spend_cap_usd: Capped.set(service, True))
def deploy(service: str, spend_cap_usd: int) -> None:
    """MOCK trusted deploy with a hard monthly spend cap.

    The provider halts ``service`` once its monthly spend reaches
    ``spend_cap_usd``. Contract: the cap is positive; how high it may go is
    an agent-stated guarantee. Records ``Capped[service] = True``.
    """
    pass


@clauz3.requires(lambda service: len(service) > 0, message="service must be non-empty")
@clauz3.trusted
@effect(lambda service: Capped.set(service, False))
def remove_spend_cap(service: str) -> None:
    """MOCK trusted removal of a service's hard spend cap.

    After this call the provider bills without limit and will not shut the
    service down. Records ``Capped[service] = False``.
    """
    pass

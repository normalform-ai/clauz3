"""Cloud-spend predicates built from generic trusted effect facts.

Inspired by Simon Willison's case for hard budget caps on by default
(https://simonwillison.net/2026/Oct/3/default-hard-budget-caps/). The provider
enforces a cap at runtime; these contracts let a user check, before anything
runs, that an agent's deploys carry caps and that the caps are sane.
"""

from tools.cloud.trusted.effects import deploy, remove_spend_cap

from clauz3.spec import ContractSpec, contract, effect

from .effects import Capped

Deploy = effect(deploy)
RemoveCap = effect(remove_spend_cap)


@contract
def cap_at_most(max_usd: int) -> ContractSpec:
    """Guarantee every deploy's monthly spend cap is at most ``max_usd``."""
    return Deploy.all(lambda d: d.spend_cap_usd <= max_usd)


@contract
def total_caps_at_most(max_usd: int) -> ContractSpec:
    """Guarantee the deploy caps sum to at most ``max_usd``.

    The sum counts every deploy, so redeploying a service counts its cap
    again; that over-approximates and fails closed. This bounds the
    worst-case monthly bill only while every service stays capped: pair it
    with ``never_remove_caps()`` or ``all_services_capped_at_end()``, since a
    later ``remove_spend_cap`` is not visible to the sum.
    """
    return Deploy.sum(lambda d: d.spend_cap_usd) <= max_usd


@contract
def only_services(allowed: list[str]) -> ContractSpec:
    """Guarantee every deploy targets a service in ``allowed``."""
    return Deploy.all(lambda d: d.service in allowed)


@contract
def never_remove_caps() -> ContractSpec:
    """Guarantee no spend cap is ever removed, even temporarily."""
    return RemoveCap.empty()


@contract
def all_services_capped_at_end() -> ContractSpec:
    """Guarantee no service is left uncapped once the program finishes.

    A cap may be removed partway through as long as a later ``deploy``
    restores one before the end.
    """
    return Capped.final.all(lambda s: s.value == True)  # noqa: E712

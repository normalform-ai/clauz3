"""LIMS / cloud-lab predicates built from generic trusted effect facts.

These contracts let an agent state policies about a wet-lab campaign before
any reagent moves:

- spatial scope (``only_plate``);
- instrument allowlist (``only_instruments``);
- reagent and runtime budgets (``reagent_volume_at_most``, ``total_runtime_at_most``);
- absence of effects (``no_protocols``, ``no_oligos``);
- sequence-content guards on synthesis orders (``oligo_length_at_most``,
  ``no_hazard_sequence``).

The sequence-content guards ride on the same z3.Contains primitive used by
``examples/text``'s ``must_not_contain``: they are the static-screening shape
biosecurity policy can attach to before submission, rather than after.
"""

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Pipette = effect("pipette")
Protocol = effect("submit_protocol")
Oligo = effect("order_oligo")


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about lab effects."""
    return core_no_guarantees()


# ── Pipette / plate scope ────────────────────────────────────────────────────


@contract
def only_plate(plate: str) -> ContractSpec:
    """Guarantee every pipette targets this plate."""
    return Pipette.all(lambda e: e.plate == plate)


@contract
def reagent_volume_at_most(reagent: str, max_ul: int) -> ContractSpec:
    """Guarantee total volume of ``reagent`` dispensed is at most ``max_ul``."""
    by_reagent = Pipette.where(lambda e: e.reagent == reagent)
    return by_reagent.sum(lambda e: e.volume_ul) <= max_ul


@contract
def total_pipette_volume_at_most(max_ul: int) -> ContractSpec:
    """Guarantee total volume across all pipette calls is at most ``max_ul``."""
    return Pipette.sum(lambda e: e.volume_ul) <= max_ul


# ── Instrument / runtime ──────────────────────────────────────────────────────


@contract
def only_instruments(allowed: list[str]) -> ContractSpec:
    """Guarantee every submitted protocol uses an instrument in ``allowed``."""
    return Protocol.all(lambda e: e.instrument in allowed)


@contract
def total_runtime_at_most(hours: int) -> ContractSpec:
    """Guarantee total instrument runtime booked is at most ``hours``."""
    return Protocol.sum(lambda e: e.hours) <= hours


@contract
def no_protocols() -> ContractSpec:
    """Guarantee no instrument protocol is submitted."""
    return Protocol.empty()


# ── Oligo / sequence content ─────────────────────────────────────────────────


@contract
def no_oligos() -> ContractSpec:
    """Guarantee no oligo is ordered."""
    return Oligo.empty()


@contract
def oligo_length_at_most(max_bases: int) -> ContractSpec:
    """Guarantee every ordered oligo is at most ``max_bases`` long."""
    return Oligo.all(lambda e: len(e.seq) <= max_bases)


@contract
def no_hazard_sequence(motif: str) -> ContractSpec:
    """Guarantee no ordered oligo contains ``motif`` as a substring.

    This is the static-screening shape for biosecurity: the prover discharges
    the guarantee before any synthesis order is submitted, so a hazard-motif
    list maintained by a biosafety officer can be enforced *before* a vendor
    sees the sequence rather than after.
    """
    return Oligo.all(lambda e: motif not in e.seq)

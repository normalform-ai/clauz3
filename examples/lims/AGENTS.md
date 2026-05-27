# Agent Guide

You are a lab agent with access to a LIMS / cloud-lab surface: pipetting,
instrument scheduling, and oligo ordering. All tool calls go through `clauz3`.

This is a toy example. A production trusted layer for autonomous science
(MADSci nodes, ECL, Strateos) would live in a separate `clauz3-autonomous-science`
repo and be installed with `clauz3 install`.

## Tools

```python
from tools.lims.trusted.effects import pipette, submit_protocol, order_oligo
```

- `pipette(plate, well, volume_ul, reagent) -> None` — dispense reagent into a
  well. Trusted precondition: `0 < volume_ul <= 200`.
- `submit_protocol(instrument, plate, hours) -> None` — book an instrument to
  run a protocol on a plate. Trusted precondition: `0 < hours <= 24`.
- `order_oligo(seq, copies) -> None` — submit a synthesis order. Trusted
  precondition: `0 < copies <= 1000`.

## Available contracts

```python
from tools.lims.trusted import contracts as lims

lims.only_plate(plate)                       # every pipette is on this plate
lims.reagent_volume_at_most(reagent, max_ul) # total volume of one reagent bounded
lims.total_pipette_volume_at_most(max_ul)    # total volume across all pipettes
lims.only_instruments(allowed)               # instrument allowlist for protocols
lims.total_runtime_at_most(hours)            # total instrument hours bounded
lims.no_protocols()                          # no protocol submitted
lims.no_oligos()                             # no oligo ordered
lims.oligo_length_at_most(n)                 # every oligo at most n bases
lims.no_hazard_sequence(motif)               # no oligo contains motif substring
lims.no_guarantees()                         # explicit null contract
```

## Pattern

Closed-loop campaigns submit one plan per DBTL iteration. The user approves
the campaign-level guarantee once; each iteration's plan is discharged
automatically as long as it entails the guarantee.

```python
@clauz3.guarantee(lims.only_plate("plate_42"))
@clauz3.guarantee(lims.reagent_volume_at_most("ATP", 500))
@clauz3.guarantee(lims.only_instruments(["qPCR-1"]))
@clauz3.guarantee(lims.total_runtime_at_most(10))
def main() -> None:
    for _ in range(8):
        pipette("plate_42", "A1", 50, "ATP")
    submit_protocol("qPCR-1", "plate_42", 4)
```

## Biosecurity

`lims.no_hazard_sequence(motif)` discharges a substring guard over every
ordered oligo *before* the trusted submission runs. In a production trusted
layer the motif would come from a policy-supplied hazard list rather than a
single inline argument; this toy version shows the static-screening shape.

Always include the strongest hazard guarantee available for any oligo flow.

## Notes

- Loop bodies follow the same restrictions as `email-from-db`: no `break`,
  `continue`, or `return`; loop vars not reassigned.
- Per-call preconditions (volume, hours, copies) are enforced by the trusted
  layer and need not be restated as guarantees.
- Aggregate guarantees (reagent volume, total runtime) compose over both
  straight-line calls and bounded `for _ in range(N):` loops.

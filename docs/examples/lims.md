# LIMS example

The LIMS example is the autonomous-science / cloud-lab analog of the
[bank example](bank.md). It exercises the same relation primitives —
allowlists, numeric aggregation, and per-field substring guards — against
a wet-lab effect vocabulary: pipetting, instrument scheduling, and oligo
ordering.

It is a toy: a production trusted layer for autonomous science (MADSci
nodes, Emerald Cloud Lab, Strateos) would live in a separate domain repo
and be installed with `clauz3 install`. See issue
[#41](https://github.com/cmungall/agent-deal/issues/41) for the bud-off
plan and [#42](https://github.com/cmungall/agent-deal/issues/42) for the
biosecurity-screening angle that the `no_hazard_sequence` contract here
gestures at.

## Trusted module

Three trusted effects, each with a per-call precondition lifted into a
proof obligation. The volume, hours, and copies bounds are written in
natural chained-comparison form (`0 < volume_ul <= 200`); see
[issue #43](https://github.com/cmungall/agent-deal/issues/43) for the
fix that made this idiomatic form actually enforce both bounds.

{{ include_file("examples/lims/tools/lims/trusted/effects.py") }}

The contract vocabulary covers plate scope, reagent and runtime budgets,
oligo-length bounds, and a substring-based hazard guard.

{{ include_file("examples/lims/tools/lims/trusted/contracts.py") }}

## A DBTL campaign

The pattern that motivates the example: an LLM-driven design-build-test-learn
loop submits one plan per iteration. The user approves the campaign-level
guarantee once; each iteration's plan is discharged automatically as long
as it entails the guarantee.

{{ include_file("examples/lims/cases/dbtl_campaign_pass.py") }}

## A biosecurity guard

`no_hazard_sequence(motif)` lowers to the same `z3.Contains` primitive
that the [text example's](text.md) `must_not_contain` uses. Before any
oligo synthesis is submitted, the prover discharges the substring guard
over every ordered sequence — exactly the static-screening shape a
biosafety officer's policy can attach to.

{{ include_file("examples/lims/cases/no_hazard_sequence_pass.py") }}

A sequence containing the hazard motif is rejected:

{{ include_file("examples/lims/cases/no_hazard_sequence_fail.py") }}

## All cases

Browse the full set on the [all-cases page](lims-all-cases.md).

## How they run

{{ include_file("examples/lims/Justfile") }}

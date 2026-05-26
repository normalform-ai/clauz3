# Quantified Aggregates (future work)

## Problem

`sum(lambda r: r.amount)` over a quantified fact requires summing
`array[i].amount` over `i in [0, length)`. Z3 doesn't natively support
unbounded symbolic sums.

v1 raises `UnsupportedError` for this case:
- The selector returning a captured constant (`lambda r: 1`,
  `lambda r: 5`) IS supported — contribution becomes `constant *
  product(quantifier.upper)`. This handles count-via-sum.
- A selector depending on a bound variable (`lambda r: r.amount`) is
  rejected.

## Routes

### A. Z3 recursive functions

Define `sum_amounts(arr, length) = If(length == 0, 0,
arr[length - 1].amount + sum_amounts(arr, length - 1))`. Z3 handles
recursive functions via fixed-point logic, but proofs are slow and
incomplete for many goals.

### B. Bounded unrolling fallback

If the trusted call's postcondition gives a literal upper bound
`len(result) <= K`, unroll the sum K times symbolically. Each term is
conditional on `i < length`. This combines the new quantifier-aware
path with bounded unrolling for aggregates specifically.

Trade-off: `K` becomes a knob; large `K` slows proofs linearly.

### C. Sequence theory

Z3's `Seq` sort has `seq.length` and `seq.nth` but limited reasoning
over predicates. Unlikely to be a general answer but worth investigating
for specific aggregate shapes.

## Recommendation

Start with (B) — bounded unrolling fallback. It composes naturally with
the existing for-loop handler and gives a working answer for realistic
`K`. Revisit (A) or (C) if unrolling proves insufficient.

# Quantified Aggregates

## Problem

`sum(lambda r: r.amount)` over a quantified fact requires summing
`array[i].amount` over `i in [0, length)`. Z3 doesn't natively support
unbounded symbolic sums.

Current handling:
- The selector returning a captured constant (`lambda r: 1`,
  `lambda r: 5`) is exact: contribution becomes `constant *
  product(quantifier.upper)`. This handles count-via-sum.
- A selector depending on a bound variable (`lambda r: r.amount`) is
  bounded by promotion (route D below, implemented).

## Routes

### D. Promotion bound (implemented)

Taken from the Bird–Meertens style of program calculation (Bird 1989,
*Algebraic Identities for Program Calculation*; replayed in Lean at
https://satnam6502.github.io/bird-meertens/). Instead of summing, rewrite
`sum ∘ map f` into facts about its extreme terms:

```
n * f(j_lo) <= sum(map f xs) <= n * f(j_hi)
forall v in [0, n): f(j_lo) <= f(v) <= f(j_hi)
```

where `n = len(xs)` and `j_lo`/`j_hi` are fresh Skolem indices in range
(skipped iterations count as a `0` term; `n == 0` gives `sum == 0`). This is
`spec._promotion_bound`. The fresh total and indices appear only under
`Implies(axioms, comparison)`, and the axioms are true of the real sum, so the
encoding is sound in either polarity.

`n * f(j)` is nonlinear, and Z3 misses the 5s proof timeout on even the bare
core. So for each literal `len(result) <= K` that appears anywhere in the
givens (a trusted `@deal.post`, also when the query runs under a branch and the
given is `Or(inactive, len <= K)`), we add the linear relaxation `K *
min(f(j_lo), 0) <= sum <= K * max(f(j_hi), 0)`, guarded by its own `len <= K`.
The guard makes each relaxation true whatever the givens say, so a candidate
that does not hold just stays inert. Independent nested loops use the product
of one cap per loop (at most 16 combinations). That makes the realistic budget
proofs fast (~2s). The selector must be `int`: a non-`int` term is refused,
because the fresh total is an `Int`.

Without any cap only the nonlinear core remains. A positive per-row bound is
then false anyway (the row count is unbounded) and Z3 finds the counterexample
quickly, but a true goal may time out to SKIP, so the end-to-end tests stick to
capped queries. `tests/test_promotion_bound.py` checks the axioms themselves on
ground instances, with no solver search.

What it buys: per-row bounds lift to totals ("<= 100 invoices, each charge
<= 10, so total <= 1000"), and loop totals compose with ordinary facts by
addition, which is fold promotion over `concat`. Only bounds the prover may
assume count: branch guards and trusted query postconditions. A trusted
callee's `@deal.pre` is an obligation, checked separately, and never
tightens the total.

What it doesn't: anything needing the exact sum, or a bound that depends on how
many rows pass a filter (the relaxation charges every row the maximum term).
Nested loops whose inner length depends on the outer row are refused,
because the row count is then a sum of inner lengths, not a product.
Routes A–C remain the options for those.

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

(D) covers budget-style guarantees. If exact totals are needed, add (B),
bounded unrolling, next. It composes with the existing for-loop handler and
can reuse `_literal_upper_bounds` to find `K`. Revisit (A) or (C) if
unrolling proves insufficient.

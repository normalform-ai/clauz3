# Quantified shares_value (future work)

## Problem

`shares_value(other, key)` is an ∃-style relation: "there exists some
value that both sides emit." Under quantification, it becomes
`∃ i, j. left[i].key == right[j].key` — Z3 handles existentials via
Skolemization, but composing with the rest of the ∀-quantified algebra
is awkward.

v1 raises `UnsupportedError` when either side has quantified facts.

## Sketch

1. Skolemize each side's quantifier — introduce a Skolem index
   `i_left` and `j_right`.
2. Translate `shares_value` to
   `0 ≤ i_left < length_left ∧ 0 ≤ j_right < length_right ∧
    left[i_left].key == right[j_right].key`.
3. Negate to test for proof; existential becomes universal.

## Trade-offs

- More Z3 quantifier alternations (∀ → ∃ → ∀) — slower proofs.
- Failure mode harder to interpret (Z3 reports "unknown" more often).

## Recommendation

Defer until a real example needs it. v1 surfaces `UnsupportedError`
pointing here.

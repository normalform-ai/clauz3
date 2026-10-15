# Email — all cases

Every case file under `examples/email/cases/`, inlined. `_pass` cases prove;
`_fail` cases are refuted; `_skip` cases leave the supported subset. Exact
conclusions are pinned in `tests/test_email_prover.py`. `policy_*` files are
approval-policy fixtures, not proof-outcome cases.

See the [curated walk-through](email.md) for context on the trusted module
and contract vocabulary.

{{ include_dir("examples/email/cases", glob="*.py") }}

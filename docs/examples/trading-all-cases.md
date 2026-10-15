# Trading — all cases

Every case file under `examples/trading/cases/`, inlined. `_pass` cases should
prove; `_fail` cases should be rejected by the prover.

See the [curated walk-through](trading.md) for the Dogwood policy these cases
port and for what the port leaves behind.

{{ include_dir("examples/trading/cases", glob="*.py") }}

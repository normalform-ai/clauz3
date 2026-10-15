# Differential testing of proved programs

The differential harness tests the soundness conclusion on concrete executions:
if a program proves OK, its observed trusted calls must satisfy its guarantees.
It also checks that each observed call, including its arguments and multiplicity,
has a corresponding effect fact in the sampled proof model.

Run the shipped example and standard-library corpus with:

```sh
uv run python -m clauz3.differential --root . --runs 8 --max-rows 3 \
  --seed 0 --json differential-report.json
```

Supply individual case paths instead of `--root` to investigate one program.
Add `--verbose` for progress after each corpus program.
The Python entry point `clauz3.differential.check_source` also accepts explicit
trusted and import roots for other programs. No trusted effect body is called:
the interpreter's `trusted_dispatch` hook supplies generated returns. Loading
trusted declarations still imports trusted modules, as ordinary proof does.

For each successful proof, the harness retains its frozen assumptions,
conditional and quantified effect facts, and symbolic trusted return values.
Z3 generates models satisfying those assumptions, with a configurable bound on
query lengths. Sampling tries empty, singleton and longer queries and varied
scalar values. Blocking clauses distinguish observable inputs, including the
rows actually returned; differences in unused array tails do not count as new
runs. A program with no variable inputs therefore runs once, even if `--runs`
is larger. The run limit is a maximum, not a claim of exhaustive coverage.

The underlying inspection API is opt-in: `prove_text` and `prove_path` accept
`capture_observations=True`. An OK result then carries `ProofResult.observation`
with the assumptions, effect facts, trusted returns, and native contract specs.
Ordinary proof callers keep the existing behavior.

Each sample runs through `clauz3.interp`. The contract oracle uses ordinary
Python predicates and collection operations over the recorded calls. It does
not call the prover's contract solver. Contract arguments are reconstructed
from the original Python literals, so a mistake in symbolic literal conversion
is not automatically repeated by the oracle. Required domain contracts are
included alongside explicit guarantees.

The JSON report records configuration, counts, exclusions, generated returns,
concrete effects, and individual findings. `contract_violation` means the
concrete trace violates a guarantee; `effect_mismatch` means an observed call
was absent from the sampled proof facts. Execution failures and unsupported
oracle operations are separate findings. Unsupported input encodings, solver
unknown/timeouts, and empty bounded model spaces are reported as incomplete.
They are never counted as successful checks. The command exits nonzero for
findings or incomplete cases. Failed, partial, skipped and vacuous proofs are
listed as `not_proved`; successful entries with parameters are listed separately
because the interpreter calls an entry without arguments.

Every sampled trusted return is also checked against the native preconditions
and postconditions for the actual call arguments. An `assumption_mismatch`
invalidates the replay; it is not reported as a contract counterexample.

The corpus sweep runs separately from the ordinary pytest suite. CI invokes
`just differential-test` on each supported Python version; use the same command
locally, or `uv run pytest -m differential_corpus tests/test_differential_corpus.py`.
The default `pytest` and `just test` commands exclude this slow test while still
running the focused harness tests. The sweep runs the CLI in a fresh process
with a smaller fixed budget on every shipped case, avoiding accumulated state
from other tests. Each solver check gets a 10-second budget in this sweep,
compared with the CLI's 2-second default. Unknown results remain incomplete,
never successful checks. Its subprocess has a 30-minute timeout and the CI step
has a 35-minute limit. The recipe streams per-case progress and the CLI output;
failures also show the JSON report path. A timeout identifies the last completed
case in the progress log.
Lower bounds on checked cases and nonempty queries prevent accidental erosion
into an all-excluded or all-empty sweep. Mutation tests deliberately introduce
an interpreter arithmetic error in several syntactic positions and require the
harness to detect the resulting contract and effect disagreement.

At introduction, the corpus contained 270 programs: 128 runnable OK proofs,
134 non-OK proofs, and 8 parameterized OK entries. With at most 4 distinct models
per program and a row bound of 2, the sweep completed **314 runs**, including
**42 nonempty query results**, with **zero findings** (contract violations,
effect mismatches, execution errors, or invalid generated inputs). Programs
without variable inputs ran once. Reproduce that budget with
`just differential 4 2`; these are bounded observations, not exhaustive coverage.
The zero-findings baseline applies to the shipped corpus. A separate focused
regression exposed the prover's use of `False` for a trusted `None` return:
the harness detected an extra effect and a contract violation. That false proof
is now refused. Void returns retain false truthiness, while unsupported value
operations fail closed; the regression requires a non-OK proof. Helpers with a
possible implicit `None` fallthrough are refused until mixed return values can
be modelled soundly.

This is bounded evidence, not a proof of the interpreter or of the trusted
implementations. The row bound and sampling budget limit explored paths.
`ColumnRef` guarantees are checked as membership in observed query column
values; a concrete trace cannot establish intensional provenance. Scalar
returns from a call inside a query loop share the proof's symbolic value across
iterations. These correlated samples are valid environments, but do not explore
independent per-iteration returns; affected runs carry a limitation in the
report. Query or container returns inside query loops, arbitrary symbolic
mapping inputs, and unsupported contract forms remain explicit gaps. Calls to
undecorated helpers in trusted modules are also incomplete: the prover inlines
them, but replay cannot safely invoke their bodies because nested effects would
bypass interception. Keep exclusions and all finding classes beside any
evaluation counts; zero contract
violations alone does not mean all proved programs ran.

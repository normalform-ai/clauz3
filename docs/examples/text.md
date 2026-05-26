# Text example

The text example shows the relation algebra's **string** side: length bounds,
substring requirements, and prefix policies. Everything here compiles to Z3's
string theory — `len(...)` becomes `z3.Length`, `x in s` becomes `z3.Contains`,
and `s.startswith(...)` becomes `z3.PrefixOf` — so the prover reasons about the
*shape* of text symbolically, across every reachable branch.

The trusted module exposes two side-effecting functions:

- `send_message(channel, text)` — post text somewhere;
- `edit_file(path, new_text)` — replace a file's contents.

The contract vocabulary then states policies an agent (or a user reviewing the
permission request) cares about:

- **lengths within limits** — `length_at_most`, `length_at_least`,
  `length_between`;
- **required or banned substrings** — `must_contain` (e.g. a mandatory
  `[automated]` footer), `must_not_contain` (e.g. a banned token);
- **regex safety before sending** — `no_regex_metacharacters`, which forbids
  every regular-expression metacharacter so agent- or user-influenced text
  cannot inject a pattern or trigger catastrophic backtracking (ReDoS)
  downstream;
- **bounded sends** — `sends_at_most`;
- **file-edit policies** — `only_edit_under` (a path-prefix sandbox),
  `edit_length_at_most` (bounded rewrites), and `no_edits`.

## Trusted module

The trusted effects are stubs decorated with `@deal.has(...)` and a
non-empty precondition. The prover lifts each precondition into a proof
obligation at every call site.

{{ include_file("examples/text/tools/text/trusted/effects.py") }}

The contract module builds the string vocabulary on top of the generic
`effect("send_message")` and `effect("edit_file")` relations. Note that the
length and substring checks live inside ordinary lambdas — the same relation
language used elsewhere, just exercising its string operators.

{{ include_file("examples/text/tools/text/trusted/contracts.py") }}

## A passing case

Both messages are within the 20-character bound, so `text.length_at_most(20)`
is discharged.

{{ include_file("examples/text/cases/length_at_most_pass.py") }}

## A failing case

Text that will be fed to a pattern matcher must be metacharacter-free; the
classic ReDoS pattern `(a+)+$` violates `text.no_regex_metacharacters()` and
the proof fails.

{{ include_file("examples/text/cases/no_regex_metacharacters_fail.py") }}

## All cases

Every file under `cases/` is a small program plus its declared guarantee.
Cases with the `_pass` suffix should prove; cases with `_fail` should be
rejected. Browse the full set on the [all-cases page](text-all-cases.md).

## How they run

The example `Justfile` invokes `clauz3 prove` against each case. `_fail`
cases are wrapped so a non-zero exit is the expected outcome.

{{ include_file("examples/text/Justfile") }}

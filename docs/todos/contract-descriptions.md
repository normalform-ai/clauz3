# Trusted contract descriptions and LinkML

## Outcome of the exploration

Use LinkML for the typed vocabulary of effect calls and contract instances.
Use a small, versioned renderer for controlled natural language (CNL). Keep
the existing prover as the authority for whether a program meets a contract.
The schema alone cannot infer the meaning of a Python function body.

The first implementation is now available: independently verified profile
requests carry deterministic descriptions and the selected trusted helpers'
full docstrings. The live card, remembered rule, and history show them beside
the original expression. See the [reference](../reference/contract-descriptions.md)
for the supported `@contract(description=...)` API and build commands.
Descriptions are saved at decision time, HTML-escaped, and never taken from
agent-supplied display metadata. Multi-paragraph scope qualifications remain
visible. Old history has no snapshot and stays code-only; loading today's
code to reinterpret yesterday's consent would be misleading. Manual-only
services and requests whose profile verification fails also remain code-only.
The profile match and receipt formats retain their existing semantics.

The [runnable export check](../prototypes/contract-linkml/demo.py) now consumes
the [generated LinkML catalog](../reference/contracts.linkml.json), containing
all 18 filesystem/editor contracts and five effect classes. It validates
parameter records, renders fixed sentences, and exports OWL, SHACL, and JSON
Schema. Production uses the same templates from trusted Python definitions;
no LinkML runtime dependency is added to clauz3.

## What the current example actually guarantees

| Expression | Faithful description |
| --- | --- |
| `rules.read_only()` | No filesystem tool modifies anything. Effects of other libraries, such as git or rsync, are not covered. |
| `rules.only_read_under('test.md')` | Every file a filesystem tool reads or lists starts with "test.md". |
| `rules.no_deletes()` | No filesystem tool deletes or moves anything away. |

These apply to the filesystem library's tools, through the events they
perform, not to every operation that touches a file. `read_only()` does not prohibit rsync, git, or another layer's
effects. Preserve the approval coverage display alongside these statements.
Do not summarize the conjunction as "the program cannot modify files".

`only_read_under` currently uses `str.startswith`. Its parameter is a string
prefix, not necessarily a relative directory. `test.md` also permits
`test.md.bak`; `/repo` also prefixes `/repository`. The effects reject parent
directory segments, but this does not make the prefix check canonical path
containment or resolve symlinks. The docstring now spells out that distinction.
If we introduce true directory-containment contracts, they should have a
distinct operation and parameter type, rather than just a friendlier label.

Universal restrictions do not promise an effect occurs: no reads also
satisfies `only_read_under`. Likewise, `no_guarantees()` must render as an
explicit absence of restrictions, never as a positive safety claim.

## Three kinds of object

| Object | LinkML representation | Example |
| --- | --- | --- |
| Effect type | Class with parameters as slots | `ReadFile(path: string)` |
| Concrete call event | Instance of an effect class | A particular read of `test.md` |
| Contract type | Class with policy parameters as slots | `FilesystemOnlyReadUnder(root: string)` |
| Contract invocation | Instance of a contract class | `{root: "test.md"}` |
| Verified claim | Record linking invocation, target, proof, trusted source | This program satisfies this instance |

The Python helper corresponds to a contract class; the helper call supplies
its slots. Its result constrains a set of effect events. It is not itself an
effect. Nor is its class automatically the OWL class of executions satisfying
that constraint. Such an execution class requires a semantic translation.

Use qualified identities and eventually stable URIs. `rules` is a local
source alias, not an identifier. Even a canonical
Python module name needs the selected layer's source identity: two installed
libraries can expose the same name with different code. The generated catalog
uses a versioned project namespace plus qualified Python symbols and includes
trusted source digests.

## Why LinkML fits

LinkML classes, inheritance, typed slots, cardinality, and metadata provide a
shared model for forms, docs, interchange, and discoverability. `description`
can carry reviewed documentation; `title` can provide a short label; mappings
can relate terms to other vocabularies. Aliases aid discovery but do not resolve
Python imports. See [schema metadata](https://linkml.io/linkml/schemas/metadata.html).

There are supported [OWL](https://linkml.io/linkml/generators/owl.html) and
[SHACL](https://linkml.io/linkml/generators/shacl.html) generators. In this
prototype they describe contract records and their required parameters. They
do not translate `root` into a restriction on every read in a program. An
annotation named `constraint_operator` is still metadata until our compiler
gives it defined semantics.

LinkML's [string_serialization](https://linkml.io/linkml-model/latest/docs/string_serialization/)
is relevant: it describes a string-valued slot derived from the containing
object's other slots using placeholders. It is not a guarantee that every
generator supplies a safe CNL engine. Prefer a presentation annotation such
as `cnl_template` for the initial prototype, keeping generated prose out of
the author-supplied contract record. A derived display slot could use
`string_serialization` later with explicit renderer support.

## Deterministic generation, in two steps

First, reviewed per-contract templates plus parameter binding:

```text
selected trusted helper + literal arguments
  -> canonical symbol + bound parameters + trusted documentation
  -> LinkML contract instance
  -> fixed template + typed literal formatting
  -> escaped approval text alongside the original expression
```

Use the existing guarantee grammar and import resolver. Bind positional and
keyword arguments to the trusted function signature, apply explicit defaults,
and validate the resulting instance. Never evaluate agent source to obtain
parameters. Preserve both source spelling and normalized values. Row-class
arguments need a schema identity; the current profile verifier deliberately
declines them. Bytes, tuples, sets, and dictionaries need explicit typed
encodings before extending the string-only spike to the full grammar.

Restrict templates to named slots; reject attribute/index access, conversions,
format specifiers, missing values, and unsupported types. Quote and escape
literal strings so a path containing a newline or braces remains a value.
Render HTML only after escaping the resulting plain text. The spike verifies
missing, mistyped, and extra parameters are rejected. Production needs a
specified total ordering for unordered containers and a locale/version policy.

Second, derive sentences from a closed contract algebra rather than keeping
templates and executable meaning in sync by hand:

```text
Empty(effect_set)                     -> No calls to X are permitted.
All(effect_set, StartsWith(path, p))   -> Every X path must start with P.
CountAtMost(effect_set, n)             -> At most N calls to X are permitted.
All(effect_set, In(field, values))     -> Every X FIELD must be one of VALUES.
```

Model that algebra in LinkML as typed expression classes. Lower the same
representation to both Z3 and CNL. Unknown expression kinds should retain
docstrings/source with an explicit missing-description indication, rather than
guessing a reassuring phrase from the function name. Existing `ContractSpec`
objects contain Python callables for predicates, so full serialization needs
the explicit predicate AST proposed in [Effect IR](effect-ir.md).

Extract mechanical facts (names, signatures, defaults, annotations,
docstrings) from the selected trusted functions. Curate semantics that Python
types do not express (path meaning, effect scope, units). Initially Python
remains authoritative and CI checks the schema against its signatures. Longer
term, generate helper wrappers from the declarative contract algebra so there
is one definition of behavior. Never maintain unrelated Python, ontology,
and prose implementations without consistency checks.

## Ontology use and its limits

A taxonomy such as `ReadFile is_a FilesystemReadEffect` is useful for browsing
and grouping. It must not implicitly broaden what an existing contract covers.
The engine intentionally moved away from shared string markers that let newly
installed effects silently join a policy. Version and bind explicit effect
sets before allowing a taxonomy to participate in approval decisions.

Exporting a vocabulary is straightforward. Exporting proof semantics needs
more work, as described in [OWL / DL mapping](owl-dl-mapping.md):

- OWL's open world does not equate missing event facts with no events. Complete
  trace closure must be explicit for absence checks.
- Distinct calls need distinct event identities; OWL does not assume different
  names denote different individuals. Call cardinality must not collapse them.
- SHACL validates a supplied graph. A valid partial trace does not establish
  that every reachable program execution is safe.
- Reachability guards, quantified calls, string predicates, per-key uniqueness,
  and sums need defined translations; some are beyond OWL DL.
- Existing exact-match profile authority must not become semantic subsumption
  merely because two records share an ontology superclass.

Keep schema validation, semantic interpretation, proof, and human presentation
as distinct operations with explicit provenance between them. Bind a future
trusted schema/template bundle by content digest alongside the trusted source
identity, and version any new consent surface before including it in receipts.
Persist the exact displayed description so a later renderer upgrade cannot
rewrite historical decisions.

## Reproduce the spike

From the repository root:

```bash
uv run --no-project --with linkml==1.12.0 python \
  docs/prototypes/contract-linkml/demo.py --output /tmp/clauz3-contract-linkml
```

This installs isolated exploratory tooling. It produces the three sentences
above, three JSON Schemas, `contracts.owl.ttl`, and `contracts.shacl.ttl`.
It checks the exported subclass and target-shape triples, and exercises
parameter validation; it does not claim proof equivalence or validate traces.
The OWL generator is configured to skip abstract-class covering axioms: by
default an abstract parent can become equivalent to the union of its known
children (and with one child, equivalent to that child). A partial catalog
should not imply that these are all possible effect types. Generator settings
are part of the export semantics, not merely output formatting.

Implemented milestone: extract the filesystem/editor catalog, bind invocation
parameters, and render trusted templates with a versioned snapshot and
cross-layer identity tests. Keeping templates in the trusted functions avoids
a second runtime metadata bundle and binds changes through existing trusted
source identity. Next, model a small closed predicate algebra and demonstrate
Z3/CNL agreement on pass/fail fixtures before pursuing semantic OWL exports or
stronger automatic approval matching.

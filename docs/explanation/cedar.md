# Cedar

[Cedar](https://www.cedarpolicy.com/) is an open-source authorization policy
language from AWS: the "who may do what to which thing" layer, factored out of
application code into standalone policies that an engine evaluates. It is
implemented in Rust under Apache 2.0, powers Amazon Verified Permissions, and is
described in Cutler et al., *Cedar: A New Language for Expressive, Fast, Safe,
and Analyzable Authorization* ([OOPSLA 2024](https://doi.org/10.1145/3649835)).

It appears in these docs for two reasons. It is the base language
[Dogwood](background.md#dogwood) extends with temporal conditions, so reading
the Dogwood section without it means reading a diff against an unknown original.
And it is the closest well-established analogue to the layer `clauz3` calls an
[approval policy](../todos/approval-policies.md) — a rule set an admin writes so
that routine requests are decided without asking a human.

## The request model is PARC

Every authorization request is four things — a **p**rincipal, an **a**ction, a
**r**esource, and a **c**ontext — and the engine answers `Allow` or `Deny`.
Application code stops asking "is this user an admin?" inline and instead asks
the engine, passing those four values.

The context slot carries per-request facts the caller supplies (`is the session
MFA-authenticated`, `what is the source IP`). It is the extension point Dogwood
later reuses: temporal results are lowered into context slots so that a Dogwood
policy is still, underneath, an ordinary Cedar policy.

## Policies

A policy is an effect — `permit` or `forbid` — a scope over PARC, and optional
`when` / `unless` conditions:

```cedar
permit (
    principal == User::"alice",
    action == Action::"view",
    resource == Photo::"VacationPhoto94.jpg"
);
```

Evaluation is order-independent, and **`forbid` wins**: one matching `forbid`
denies the request regardless of how many `permit`s also matched. That is what
makes guardrails composable — a security admin can drop in a blanket deny
without auditing every `permit` in the system, and no later policy can
accidentally re-open the hole.

```cedar
forbid (principal, action, resource)
when { resource.private }
unless { principal == resource.owner };
```

The same "deny wins" discipline is why the `clauz3` approval policy evaluates
every `auto_rejected` rule before any `auto_approved` rule
([precedence and fallbacks](../todos/approval-policies.md)); an allow-list must
never be able to override a block-list.

## Entities and the hierarchy

Principals, actions, and resources are typed **entities** carrying attributes,
so conditions read as ordinary data access:

```cedar
permit (principal, action == Action::"editPhoto", resource)
when { resource.owner == principal };
```

Entities also form a hierarchy that `in` walks. This is how groups, albums, and
folders work without enumerating members into the policy:

```cedar
permit (
    principal in Group::"alice_friends",
    action in [Action::"listPhotos", Action::"view"],
    resource in Album::"alice_vacation"
);
```

The scope supports `==` for a specific entity, `in` for hierarchy membership,
`is` for type matching (`principal is User`), and combinations
(`principal is User in Group::"alice_friends"`). Bare `principal`, `action`, or
`resource` matches anything.

Cedar covers role-based, attribute-based, and relationship-based access control
with these pieces: the hierarchy gives RBAC and ReBAC, the attribute conditions
give ABAC.

## Schema, validation, and templates

A **schema** declares the entity types, their attributes, the available actions,
and which principal and resource types each action applies to. With one in hand,
a validator can reject a policy that references a nonexistent attribute or
applies an action to a resource type that can never receive it — errors caught
at authoring time rather than as a silent `Deny` in production.

**Templates** carry `?principal` / `?resource` slots and are *linked* against
concrete entities:

```cedar
permit (
    principal == ?principal,
    action == Action::"view",
    resource in ?resource
);
```

"Share this album with Bob" becomes a link record rather than freshly generated
policy text, which keeps the number of distinct policies small even when the
number of grants is large.

## Small on purpose

Cedar is deliberately not Turing-complete: no recursion, no unbounded loops,
total evaluation. Every policy terminates, and the language stays inside a
fragment an SMT solver can reason about.

That restraint is what buys **automated reasoning about policies themselves**,
as opposed to about a single request. The analysis tooling answers questions a
test suite cannot: is this policy always denied (dead policy), does this
refactor change any decision for any possible request, does policy set A permit
strictly less than policy set B. Cedar's engine and validator are formally
modeled in Lean with key properties proved in its proof assistant, and the Rust
implementation is checked against that model by differential testing — the
verification-guided development the OOPSLA paper describes.

This is the capability [Dogwood](background.md#dogwood) explicitly gives up at
the boundary: temporal conditions do not yet get Cedar's analysis tools.

## What Cedar deliberately does not do

PARC is a *single request*. Cedar can express "Alice may sell shares if she owns
the account and the session is MFA-authenticated," but nothing in the model
refers to a previous request. There is no vocabulary for:

- **prerequisites** — only if a matching approval came back within the hour
- **rate and volume limits** — no more than $5000 transferred this hour
- **ordering** — this action must follow that one, never precede it
- **freshness** — the balance was read less than a minute ago

Every one of those needs history, and history is exactly what a point-in-time
authorization model does not have. That gap is why Dogwood exists, and its
`when temporal { ... }` clause is a surgical addition to a language whose every
other feature assumes there is nothing to remember.

## How this relates to `clauz3`

Cedar and `clauz3` answer different questions about different objects, and the
difference is easy to lose because both produce a yes/no:

| | Cedar | `clauz3` |
| --- | --- | --- |
| Unit of decision | one request (PARC) | one program |
| Question | may this call happen? | what will this program provably do? |
| Decided from | attributes of principal, resource, context | a Z3 proof over the program's trusted effect facts |
| Written by | an operator, up front | the agent, per program, and checked |
| Timing | at the call | before any effect runs |

The Cedar-shaped layer inside `clauz3` is the
[approval policy](../todos/approval-policies.md): a policy admin's rules let the
approval service resolve routine requests without waking a human. Three things
about it are worth putting next to Cedar.

**It decides by entailment, not by matching.** A Cedar condition inspects
attributes attached to the request. An approval-policy clause is a *contract
expression* that gets conjoined onto the program and re-proved, so a rule fires
whenever the program logically implies it — a program proving `none()` entails
`at_most(10)` for free, and one emailing a single allow-listed literal entails
`only([...])` and `unique_recipients()` without stating either. The block-list
direction is stronger still: "reject unless you have proven you avoid the CEO"
rejects any program that *cannot discharge* the obligation, including one whose
recipient is data-dependent under `no_guarantees()`. There is no attribute on a
Cedar request that carries the same weight, because the thing being judged is a
program rather than a call.

**Its per-request scoping is the same Cedar limitation, for the same reason.**
The approval-policy doc lists it plainly: entailment is per-program and
single-request, the service keeps no ledger across requests, and "at most 10
emails to bob *per day* across many runs" is not expressible. That is Cedar's
missing dimension arriving in `clauz3` by an independent route — and Dogwood is
the worked answer to it one layer down. Whatever a stateful approval policy
eventually looks like, the design space is the one Dogwood already mapped:
windowed aggregates over an authenticated event history.

**It has no policy analysis.** Cedar's headline safety property is that an admin
can ask whether a rule is dead, redundant, or subsumed by another before
shipping it. `clauz3` policy rules are ordered lists of clause expressions with
no such analysis: nothing reports that `auto-pass-internal` can never fire
because an earlier block rule always catches it, or that two rules are
equivalent. The prover machinery to answer those questions exists — subsumption
between contract expressions is an entailment check, the same primitive the
service already runs — and the
[Datalog frontend note](background.md#mechanics-worth-borrowing) in the
background doc is where the idea already lives. Cedar is the evidence that it is
worth building.

One inversion is worth stating, because it cuts against the whole comparison.
Cedar policies are authored by operators and applied to subjects who have no say
in them. `clauz3`'s primary surface is the opposite: the agent *proposes* the
contract, and the user accepts, rejects, or demands a stronger one (see
[operator policy versus agent-abduced contracts](background.md#operator-policy-versus-agent-abduced-contracts)).
The approval policy is the one place `clauz3` adopts the operator posture, which
is precisely why Cedar is the right reference point for that layer and the wrong
one for the rest.

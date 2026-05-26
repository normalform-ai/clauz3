# Email-from-DB example

This example combines two trusted effects: `db_query` returns a bounded list
of rows from a typed schema, and `send_email` sends to a given address. Agent
code iterates over the query result and sends to each row's `email` field —
the prover symbolically unrolls the loop and checks that every recipient
came from the right column.

It's the smallest example that exercises symbolic iteration over a
trusted-call return value plus column-binding constraints.

## Trusted module

The trusted effects expose one read effect (`db_query`, marked `db_read`)
and one write effect (`send_email`). `db_query`'s `@deal.post` caps the
result length, which is what lets the prover bound the loop.

{{ include_file("examples/email-from-db/tools/db/trusted/effects.py") }}

The schema is a `clauz3.Row` subclass — its fields become the row schema
the prover binds against when iterating the query result.

{{ include_file("examples/email-from-db/tools/db/trusted/schemas.py") }}

The contract module adds two domain helpers on top of the generic
`effect("send_email")` and `effect("db_query")` relations:
`addresses_from(schema, field)` binds every recipient to a column of a
schema, and `count_at_most(n)` is a simple aggregate on the email
relation.

{{ include_file("examples/email-from-db/tools/db/trusted/contracts.py") }}

## A passing case

Looping over the consented users and emailing each one's `email` column
discharges both `addresses_from(UserRow, "email")` and
`count_at_most(100)` (the latter from `db_query`'s `@deal.post` length
cap).

{{ include_file("examples/email-from-db/cases/newsletter_pass.py") }}

## A failing case

Asserting `emails.none()` and then sending inside a loop over `db_query(...)`
is rejected: the loop can produce at least one reachable `send_email` fact.

{{ include_file("examples/email-from-db/cases/email_loop_fail.py") }}

## All cases

Browse the full set on the [all-cases page](email-from-db-all-cases.md).

## How they run

{{ include_file("examples/email-from-db/Justfile") }}

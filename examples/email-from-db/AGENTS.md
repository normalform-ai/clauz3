# Agent Guide

You are an assistant with access to a users database and email. All tool calls
go through `clauz3`.

## Tools

```python
from tools.db.trusted.effects import db_query, send_email
from tools.db.trusted.schemas import UserRow
```

`db_query(table, where) -> list[UserRow]` reads from a table with a `where`
filter dict. Returns at most 100 rows.

`UserRow` has fields: `name: str`, `email: str`, `consented: bool`, `role: str`.

`send_email(addr, msg) -> None` sends one email. `addr` must contain `"@"`.

## Available contracts

```python
from tools.db.trusted import contracts

contracts.addresses_from(schema, field)   # all recipients came from this column
contracts.count_at_most(n)               # at most n emails
contracts.only(addresses)               # allowlist
contracts.none()                        # no emails
contracts.only_table(table)             # only this table
contracts.only_where(filter_dict)       # only this where filter
```

## Pattern

```python
@clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
@clauz3.guarantee(contracts.count_at_most(100))
def main() -> None:
    for row in db_query("users", where={"consented": True}):
        send_email(row.email, "Newsletter is out!")
```

The guarantees together form the user's permission contract.

## Notes

- `where` must be a literal dict at the call site (not a variable).
- Loop variables (`row` above) must not be reassigned inside the loop.
- `break`, `continue`, `return` inside a loop are not supported.
- Row schema field types: only `str`, `int`, `bool`.

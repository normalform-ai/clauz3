"""Application-database schemas for the same-name collision fixture."""

import clauz3


class UserRow(clauz3.Row):
    """Rows of the application ``users`` table."""

    name: str
    email: str

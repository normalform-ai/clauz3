"""Directory schemas that share a bare class name with ``dbt.rows``."""

import clauz3


class UserRow(clauz3.Row):
    """Rows of the corporate LDAP directory — a different domain entirely."""

    name: str
    email: str

"""Directory effects for the same-name collision fixture."""

import deal

from ldapt.rows import UserRow


@deal.has("ldap_read")
@deal.post(lambda result: len(result) <= 100)
def ldap_query(base_dn: str) -> list[UserRow]:
    """Trusted LDAP query returning up to 100 directory UserRows."""
    return []

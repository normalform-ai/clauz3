import deal
from qtrusted.rows import InvoiceRow, UserRow


@deal.has("email")
def send_email(addr: str, msg: str) -> None:
    """Trusted email sender."""
    pass


@deal.pre(
    lambda addr, msg: addr == "bob@example.com",
    message="addr must equal bob@example.com",
)
@deal.has("email")
def send_email_only_to_bob(addr: str, msg: str) -> None:
    """Trusted email sender — precondition requires addr == 'bob@example.com'."""
    pass


@deal.has("db_read")
@deal.post(lambda result: len(result) <= 5)
def db_query(table: str) -> list[UserRow]:
    """Trusted DB query returning a bounded list of UserRows."""
    return []


@deal.has("db_read")
@deal.post(lambda result: len(result) <= 100)
def db_query_users100(table: str) -> list[UserRow]:
    """Trusted DB query returning up to 100 UserRows."""
    return []


@deal.has("db_read")
@deal.post(lambda result: len(result) <= 100)
def db_query_invoices(table: str) -> list[InvoiceRow]:
    """Trusted DB query returning up to 100 InvoiceRows."""
    return []


@deal.has("billing")
def charge(amount: int) -> None:
    """MOCK trusted charge."""
    pass

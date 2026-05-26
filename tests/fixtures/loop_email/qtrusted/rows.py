import clauz3


class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool


class InvoiceRow(clauz3.Row):
    amount: int

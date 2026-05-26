import clauz3


class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool
    role: str

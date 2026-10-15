import clauz3


class ApprovalRow(clauz3.Row):
    """A sale approval recorded by the trading desk's approval system.

    ``approved`` is the desk's decision. Rows exist for rejected requests too,
    which is why the ``where`` filter on a query is load-bearing.
    """

    stock: str
    shares: int
    approver: str
    approved: bool

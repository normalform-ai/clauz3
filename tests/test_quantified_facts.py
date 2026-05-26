"""Direct introspection of facts emitted by the symbolic executor."""

from __future__ import annotations

import textwrap

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._context._layer import FactInfo
from clauz3._vendor.deal_solver._eval_stmt import eval_stmt
from clauz3.prover import AgentTheorem


def _facts_for(source: str) -> list[FactInfo]:
    """Run symbolic execution on `source`'s main() and return emitted facts."""
    text = textwrap.dedent(source)
    theorems = list(AgentTheorem.from_text(text))
    main_theorem = next(t for t in theorems if t.name == "main")
    main_node = main_theorem._func
    ctx = Context.make_empty(
        get_contracts=main_theorem.get_contracts,
        get_guarantees=main_theorem.get_guarantees,
    )
    for stmt in main_node.body:
        eval_stmt(stmt, ctx=ctx)
    return list(ctx.facts)


def test_for_loop_fact_has_one_quantifier() -> None:
    facts = _facts_for(
        """
        import deal
        import clauz3


        class UserRow(clauz3.Row):
            email: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 100)
        def db_query(table: str) -> list[UserRow]: ...


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @clauz3.guarantee(lambda: True)
        def main() -> None:
            for row in db_query("users"):
                send_email(row.email, "hi")
    """
    )
    email_facts = [f for f in facts if f.name == "send_email"]
    assert len(email_facts) == 1
    assert len(email_facts[0].quantifiers) == 1
    assert email_facts[0].quantifiers[0].source.row_schema.__name__ == "UserRow"


def test_nested_for_loops_emit_two_quantifiers() -> None:
    facts = _facts_for(
        """
        import deal
        import clauz3


        class UserRow(clauz3.Row):
            email: str


        class ContactRow(clauz3.Row):
            message: str


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 10)
        def get_users(table: str) -> list[UserRow]: ...


        @deal.has("db_read")
        @deal.post(lambda result: len(result) <= 10)
        def get_contacts(table: str) -> list[ContactRow]: ...


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @clauz3.guarantee(lambda: True)
        def main() -> None:
            for u in get_users("users"):
                for c in get_contacts("contacts"):
                    send_email(u.email, c.message)
    """
    )
    email_facts = [f for f in facts if f.name == "send_email"]
    assert len(email_facts) == 1
    assert len(email_facts[0].quantifiers) == 2
    assert email_facts[0].quantifiers[0].source.row_schema.__name__ == "UserRow"
    assert email_facts[0].quantifiers[1].source.row_schema.__name__ == "ContactRow"

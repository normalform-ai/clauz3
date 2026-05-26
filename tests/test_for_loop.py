import pytest
import z3

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._context._layer import FactInfo
from clauz3._vendor.deal_solver._context._quantifier import Quantifier
from clauz3._vendor.deal_solver._eval_stmt import eval_stmt
from clauz3._vendor.deal_solver._proxies._row import QueryResultSort
from clauz3.prover import AgentTheorem
from clauz3.row import Row


class UserRow(Row):
    name: str
    email: str


def _facts_for(source: str) -> list[FactInfo]:
    theorems = list(AgentTheorem.from_text(source))
    main_theorem = next(t for t in theorems if t.name == "main")
    ctx = Context.make_empty(
        get_contracts=main_theorem.get_contracts,
        get_guarantees=main_theorem.get_guarantees,
    )
    for stmt in main_theorem._func.body:
        eval_stmt(stmt, ctx=ctx)
    return list(ctx.facts)


def test_quantifier_has_bounds_helpers() -> None:
    arr = z3.Const("users", z3.ArraySort(z3.IntSort(), z3.IntSort()))
    length = z3.Int("len")
    i = z3.Int("i")
    qr = QueryResultSort(
        row_schema=UserRow,
        array_expr=arr,
        length_expr=length,
        source=("db_query", {}),
    )
    q = Quantifier(bound_var=i, source=qr, lower=z3.IntVal(0), upper=length)
    bounds = q.bounds_expr()
    assert isinstance(bounds, z3.BoolRef)


def test_fact_info_quantifiers_defaults_to_empty() -> None:
    fact = FactInfo(name="x", markers=(), args={}, cond=None)  # type: ignore[arg-type]
    assert fact.quantifiers == ()


def test_fact_info_accepts_quantifiers_tuple() -> None:
    arr = z3.Const("a", z3.ArraySort(z3.IntSort(), z3.IntSort()))
    qr = QueryResultSort(
        row_schema=UserRow,
        array_expr=arr,
        length_expr=z3.Int("n"),
        source=("db_query", {}),
    )
    q = Quantifier(
        bound_var=z3.Int("i"),
        source=qr,
        lower=z3.IntVal(0),
        upper=z3.Int("n"),
    )
    fact = FactInfo(
        name="x",
        markers=(),
        args={},
        cond=None,  # type: ignore[arg-type]
        quantifiers=(q,),
    )
    assert fact.quantifiers == (q,)


def test_context_has_quantifiers_layer() -> None:
    ctx = Context.make_empty(
        get_contracts=lambda x: iter(()),
        get_guarantees=lambda x: iter(()),
    )
    assert hasattr(ctx, "quantifiers")
    assert list(ctx.quantifiers) == []


def test_context_quantifiers_add() -> None:
    ctx = Context.make_empty(
        get_contracts=lambda x: iter(()),
        get_guarantees=lambda x: iter(()),
    )
    arr = z3.Const("a", z3.ArraySort(z3.IntSort(), z3.IntSort()))
    qr = QueryResultSort(
        row_schema=UserRow,
        array_expr=arr,
        length_expr=z3.Int("n"),
        source=("db_query", {}),
    )
    q = Quantifier(
        bound_var=z3.Int("i"),
        source=qr,
        lower=z3.IntVal(0),
        upper=z3.Int("n"),
    )
    ctx.quantifiers.add(q)
    assert list(ctx.quantifiers) == [q]


def test_for_loop_over_symbolic_range_raises() -> None:
    """range(N) is only supported when N is a literal integer."""
    source = """
import clauz3

@clauz3.guarantee(lambda: True)
def main() -> None:
    n = 5
    for x in range(n):
        pass
"""
    from clauz3.prover import prove_text

    results = prove_text(source)
    assert not all(r.ok for r in results)
    error_str = " ".join(str(r.proof.error or "") for r in results)
    assert "range" in error_str.lower() or "unsupported" in error_str.lower()


def test_for_loop_over_literal_range_unrolls_to_concrete_facts() -> None:
    facts = _facts_for(
        """
        import deal
        import clauz3


        @deal.has("record")
        def record(i: int) -> None: ...


        @clauz3.guarantee(lambda: True)
        def main() -> None:
            for i in range(3):
                record(i)
        """
    )

    records = [fact for fact in facts if fact.name == "record"]
    assert len(records) == 3
    assert all(fact.quantifiers == () for fact in records)
    assert [fact.args["i"].expr.as_long() for fact in records] == [0, 1, 2]


def test_for_loop_over_literal_list_unrolls_to_concrete_facts() -> None:
    facts = _facts_for(
        """
        import deal
        import clauz3


        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...


        @clauz3.guarantee(lambda: True)
        def main() -> None:
            for addr in ["bob@example.com", "ann@example.com"]:
                send_email(addr, "hi")
        """
    )

    emails = [fact for fact in facts if fact.name == "send_email"]
    assert len(emails) == 2
    assert all(fact.quantifiers == () for fact in emails)
    assert [fact.args["addr"].expr.as_string() for fact in emails] == [
        "bob@example.com",
        "ann@example.com",
    ]


def test_for_loop_over_query_result_emits_quantified_fact() -> None:
    """For-loop over a trusted list[Row] return runs the body symbolically
    and emits facts with the quantifier snapshot."""
    source = """
import deal
import clauz3


class UserRow(clauz3.Row):
    name: str
    email: str
    consented: bool


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
    from clauz3.prover import prove_text

    results = prove_text(source)
    assert all(r.ok for r in results), [r.proof.error for r in results]


@pytest.mark.parametrize(
    "body",
    [
        "break",
        "continue",
    ],
)
def test_for_loop_break_continue_raise_unsupported(body: str) -> None:
    source = f"""
import deal
import clauz3


class UserRow(clauz3.Row):
    email: str


@deal.has("db_read")
def db_query(table: str) -> list[UserRow]: ...


@clauz3.guarantee(lambda: True)
def main() -> None:
    for row in db_query("users"):
        {body}
"""
    from clauz3.prover import prove_text

    results = prove_text(source)
    assert not all(r.ok for r in results)
    error_str = " ".join(str(r.proof.error or "") for r in results)
    assert body in error_str.lower() or "unsupported" in error_str.lower()


def test_for_loop_return_in_body_raises_unsupported() -> None:
    """A `return` statement inside a for-loop is rejected in v1."""
    source = """
import deal
import clauz3


class UserRow(clauz3.Row):
    email: str


@deal.has("db_read")
def db_query(table: str) -> list[UserRow]: ...


@clauz3.guarantee(lambda: True)
def main() -> None:
    for row in db_query("users"):
        return
"""
    from clauz3.prover import prove_text

    results = prove_text(source)
    assert not all(r.ok for r in results)
    error_str = " ".join(str(r.proof.error or "") for r in results)
    assert "return" in error_str.lower() or "unsupported" in error_str.lower()

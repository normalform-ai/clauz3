"""Regression tests for the prover / Z3-mapping review findings (#123 - #133).

Every test here is a program (or formula shape) that the review found the
prover to get wrong. Each was reproduced against the shipped fixtures before
the corresponding fix landed. Negative cases pin the exact conclusion: a
refutation is ``FAIL`` and a refusal is ``SKIP`` with its message, so a test
cannot drift into passing on the wrong kind of non-``OK``.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest
import z3

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProofResult, ProverConfigError, prove_text

FIXTURES = Path(__file__).resolve().parent / "fixtures/loop_email"
TRUSTED = FIXTURES / "qtrusted"


def _prove(source: str) -> list[ProofResult]:
    return prove_text(
        textwrap.dedent(source),
        trusted_roots=[TRUSTED],
        import_paths=[FIXTURES],
        source_name="<review>",
    )


def _ok(source: str) -> bool:
    results = _prove(source)
    assert results, "no theorem found"
    return all(r.ok for r in results)


def _conclusions(source: str) -> list[Conclusion]:
    return [r.proof.conclusion for r in _prove(source)]


def _fails(source: str) -> bool:
    """The program is *refuted*: exactly one theorem, concluding FAIL."""
    return _conclusions(source) == [Conclusion.FAIL]


def _skips(source: str, *, match: str) -> bool:
    """The program is *refused*: exactly one theorem, SKIP naming ``match``."""
    results = _prove(source)
    return [r.proof.conclusion for r in results] == [Conclusion.SKIP] and match in str(
        results[0].proof.error
    )


# ── #123 loop-carried state ──────────────────────────────────────────────────


def test_123_loop_counter_cannot_hide_effect() -> None:
    assert _skips(
        match="bound outside the loop",
        source="""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import db_query_users100, send_email

        @clauz3.guarantee(contracts.at_most(0))
        def main() -> None:
            n = 0
            for row in db_query_users100("users"):
                n = n + 1
                if n > 2:
                    send_email(row.email, "hi")
    """,
    )


def test_123_post_loop_variable_empty_loop_path() -> None:
    assert _skips(
        match="bound outside the loop",
        source="""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import db_query_users100, send_email
        from qtrusted.rows import UserRow

        @clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
        def main() -> None:
            target = "attacker@example.com"
            for row in db_query_users100("users"):
                target = row.email
            send_email(target, "hi")
    """,
    )


def test_123_accumulator_is_not_one_row() -> None:
    assert _skips(
        match="bound outside the loop",
        source="""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import charge, db_query_invoices

        @clauz3.guarantee(contracts.total_amount_under(10))
        def main() -> None:
            total = 0
            for inv in db_query_invoices("invoices"):
                if inv.amount <= 10:
                    total = total + inv.amount
            charge(total)
    """,
    )


def test_123_loop_invariant_reads_still_prove() -> None:
    """Reading an outer variable inside the body stays supported."""
    assert _ok("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import db_query_users100, send_email

        @clauz3.guarantee(contracts.at_most(100))
        def main() -> None:
            greeting = "hi"
            for row in db_query_users100("users"):
                send_email(row.email, greeting)
    """)


# ── #124 trusted returns modelled as concrete True ───────────────────────────


def test_124_negated_trusted_bool_return_does_not_hide_effect() -> None:
    assert _fails("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import is_allowed, send_email

        @clauz3.guarantee(contracts.no_emails())
        def main() -> None:
            if not is_allowed("x"):
                send_email("victim@example.com", "hi")
    """)


def test_124_trusted_str_return_compared_to_literal() -> None:
    assert _fails("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import lookup_name, send_email

        @clauz3.guarantee(contracts.no_emails())
        def main() -> None:
            name = lookup_name("k")
            if name == "":
                send_email("victim@example.com", "hi")
    """)


def test_124_negated_none_return_does_not_hide_effect() -> None:
    assert _fails("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import send_email

        @clauz3.guarantee(contracts.at_most(1))
        def main() -> None:
            if not send_email("a@example.com", "hi"):
                send_email("b@example.com", "hi")
    """)


# ── #125 same-line lambdas ───────────────────────────────────────────────────


def test_125_same_line_where_all_lambdas_compile_separately() -> None:
    assert _fails("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import send_email

        @clauz3.guarantee(contracts.bob_gets_x())
        def main() -> None:
            send_email("bob@example.com", "y")
    """)


def test_125_fixture_lambdas_share_one_line() -> None:
    """The #125 fixture is load-bearing only while both lambdas share a line.

    Reads the file rather than importing ``qtrusted``, so the guard does not
    depend on an earlier test having left the fixture root in ``sys.modules``.
    """
    tree = ast.parse((TRUSTED / "contracts.py").read_text())
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "bob_gets_x"
    )
    lambdas = [node for node in ast.walk(func) if isinstance(node, ast.Lambda)]
    assert len(lambdas) == 2
    assert len({node.lineno for node in lambdas}) == 1, [n.lineno for n in lambdas]


def test_125_same_line_lambdas_positive_case_still_proves() -> None:
    assert _ok("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import send_email

        @clauz3.guarantee(contracts.bob_gets_x())
        def main() -> None:
            send_email("bob@example.com", "x")
            send_email("ann@example.com", "y")
    """)


# ── #126 distinct cross-fact binders ─────────────────────────────────────────


def _quantifier_body_var_indices(q: z3.QuantifierRef) -> set[int]:
    seen: set[int] = set()
    pending = [q.body()]
    while pending:
        e = pending.pop()
        if z3.is_var(e):
            seen.add(z3.get_var_index(e))
        elif z3.is_quantifier(e):
            continue
        else:
            pending.extend(e.children())
    return seen


def test_126_distinct_cross_fact_clause_uses_both_binders() -> None:
    """Pin the clause shape: both sides get their own binder.

    This is deliberately a formula-level pin. A conclusion-level one is not
    constructible today: query rows are unconstrained, so the per-fact
    same-fact clause already refutes every ``distinct`` over a loop, and
    the old single-binder cross-fact clause could only be told apart once
    a trusted postcondition can assert per-row uniqueness.
    """
    from clauz3._vendor.deal_solver._context import Context
    from clauz3._vendor.deal_solver._eval_stmt import eval_stmt
    from clauz3.prover import AgentTheorem
    from clauz3.spec import effect

    source = textwrap.dedent("""
        import deal
        import clauz3

        class ContactRow(clauz3.Row):
            email: str
            name: str

        @deal.has("db_read")
        def db_query(table: str) -> list[ContactRow]: ...

        @deal.has("email")
        def send_email(addr: str, msg: str) -> None: ...

        @clauz3.guarantee(lambda: True)
        def main() -> None:
            for row in db_query("users"):
                send_email(row.email, "a")
                send_email(row.name, "b")
    """)
    theorem = next(t for t in AgentTheorem.from_text(source) if t.name == "main")
    ctx = Context.make_empty(
        get_contracts=theorem.get_contracts,
        get_guarantees=theorem.get_guarantees,
    )
    for stmt in theorem._func.body:
        eval_stmt(stmt, ctx=ctx)
    expr = effect("send_email").distinct(lambda e: e.addr).solve(ctx=ctx).expr

    quantifiers = [c for c in expr.children() if z3.is_quantifier(c)]
    assert len(quantifiers) == 3  # one cross-fact clause, two same-fact clauses
    for q in quantifiers:
        assert q.num_vars() == 2
        assert _quantifier_body_var_indices(q) == {0, 1}, q.sexpr()


def test_126_distinct_over_loop_with_container_arg_does_not_crash() -> None:
    """A loop-invariant list argument on a quantified fact passes through.

    The literal mentions no loop index, so substitution is a no-op and the
    proxy is returned untouched (rebuilding it used to trip an AssertionError
    that escaped prove()). The proof is expected to fail (rows are
    unconstrained), never to raise.
    """
    results = _prove("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import db_query, send_bulk

        @clauz3.guarantee(contracts.unique_bulk_recipients())
        def main() -> None:
            for row in db_query("users"):
                send_bulk(row.email, ["audit@example.com"])
    """)
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


def test_126_distinct_mixed_quantified_and_straight_line_pair() -> None:
    """A looped fact paired with a straight-line one is refuted, not refused."""
    results = _prove("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import db_query, send_email

        @clauz3.guarantee(contracts.unique_recipients())
        def main() -> None:
            send_email("bob@example.com", "hi")
            for row in db_query("users"):
                send_email(row.email, "hi")
    """)
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


def test_126_distinct_over_loop_with_row_dependent_container_arg() -> None:
    """A container that mentions the loop index is rebuilt with its metadata.

    ``[row.name]`` changes under substitution, so this is the one path through
    ``evolve``; the typed list must keep its subtypes and the proof must be a
    refutation, not a crash or a refusal.
    """
    assert _fails("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import db_query, send_bulk

        @clauz3.guarantee(contracts.unique_bulk_recipients())
        def main() -> None:
            for row in db_query("users"):
                send_bulk(row.email, [row.name])
    """)


@pytest.mark.parametrize("literal", ["[]", "()", "{}", "set()"])
def test_126_distinct_over_loop_with_untyped_container_arg(literal: str) -> None:
    """Empty containers are untyped proxies that cannot be rebuilt.

    ``[]``/``()``/``set()`` raise NotImplementedError from ``subtypes`` or
    ``evolve`` and ``{}`` has no ``subtypes`` at all, so rebuilding them under
    substitution escaped prove(). They mention no loop index, so substitution
    is a no-op and they must pass through untouched.
    """
    results = _prove(f"""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import db_query, send_bulk_any

        @clauz3.guarantee(contracts.unique_bulk_any_recipients())
        def main() -> None:
            for row in db_query("users"):
                send_bulk_any(row.email, {literal})
    """)
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]


# ── #127 datatype naming ─────────────────────────────────────────────────────


def test_127_same_named_row_classes_get_distinct_sorts() -> None:
    from clauz3._vendor.deal_solver._proxies._row import z3_datatype_for_row
    from clauz3.row import Row

    first = type(
        "ReviewRow",
        (Row,),
        {"__annotations__": {"email": str}, "__module__": "a"},
    )
    second = type(
        "ReviewRow",
        (Row,),
        {"__annotations__": {"email": str, "name": str}, "__module__": "b"},
    )
    s1 = z3_datatype_for_row(first)
    s2 = z3_datatype_for_row(second)
    assert not s1.eq(s2)
    assert s1.constructor(0).arity() == 1
    assert s2.constructor(0).arity() == 2


# ── #128 given dropped in branches ───────────────────────────────────────────


def test_128_query_under_branch_keeps_length_postcondition() -> None:
    assert _ok("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import db_query_users100, send_email

        @clauz3.guarantee(contracts.at_most(100))
        def main(flag: bool) -> None:
            if flag:
                for row in db_query_users100("users"):
                    send_email(row.email, "hi")
    """)


# ── #129 aliased guarantee decorator ─────────────────────────────────────────


def test_129_module_alias_guarantee_is_proved() -> None:
    assert _fails("""
        import clauz3 as c
        from qtrusted import contracts
        from qtrusted.effects import send_email

        @c.guarantee(contracts.no_emails())
        def main() -> None:
            send_email("x@example.com", "hi")
    """)


def test_129_from_import_alias_guarantee_is_proved() -> None:
    assert _fails("""
        from clauz3 import guarantee as g
        from qtrusted import contracts
        from qtrusted.effects import send_email

        @g(contracts.no_emails())
        def main() -> None:
            send_email("x@example.com", "hi")
    """)


# ── #130 guarantee expression shape ──────────────────────────────────────────


def test_130_agent_defined_helper_is_not_a_guarantee() -> None:
    with pytest.raises(ProverConfigError):
        _prove("""
            import clauz3
            from qtrusted.effects import send_email

            def helper() -> bool:
                return True

            @clauz3.guarantee(helper())
            def main() -> None:
                send_email("anyone@example.com", "hi")
        """)


def test_130_guarantee_argument_cannot_name_a_local() -> None:
    with pytest.raises(ProverConfigError):
        _prove("""
            import clauz3
            from qtrusted import contracts
            from qtrusted.effects import send_email

            @clauz3.guarantee(contracts.at_most(n))
            def main() -> None:
                n = 5
                send_email("anyone@example.com", "hi")
        """)


def test_130_registered_contract_with_literal_args_is_accepted() -> None:
    assert _ok("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import db_query, send_email
        from qtrusted.rows import UserRow

        @clauz3.guarantee(contracts.addresses_from(UserRow, "email"))
        @clauz3.guarantee(contracts.at_most(5))
        def main() -> None:
            for row in db_query("users"):
                send_email(row.email, "hi")
    """)


# ── #131 chained assignment ──────────────────────────────────────────────────


def test_131_chained_assignment_records_one_fact() -> None:
    assert _ok("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import send_email

        @clauz3.guarantee(contracts.at_most(1))
        @clauz3.guarantee(contracts.at_least(1))
        def main() -> None:
            a = b = send_email("x@example.com", "hi")
    """)


# ── #132 free bound variable in count()/sum() ────────────────────────────────


def test_132_row_dependent_count_upper_bound_proves_at_length_bound() -> None:
    assert _ok("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import db_query_users100, send_email

        @clauz3.guarantee(contracts.at_most(100))
        def main() -> None:
            for row in db_query_users100("users"):
                if row.consented:
                    send_email(row.email, "hi")
    """)


def test_132_row_dependent_count_lower_bound_does_not_prove() -> None:
    """``count() >= 1`` must not prove when every row may be unconsented."""
    assert _fails("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import db_query_users100, send_email

        @clauz3.guarantee(contracts.at_least(1))
        def main() -> None:
            for row in db_query_users100("users"):
                if row.consented:
                    send_email(row.email, "hi")
    """)


# ── #133 exceptions escaping prove() ─────────────────────────────────────────


def test_133_contract_helper_exception_is_a_skip_not_a_traceback() -> None:
    # ``getattr(UserRow, "nosuch")`` raises AttributeError inside the trusted
    # contract body; that must surface as a failed/skipped proof, not escape.
    results = _prove("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import send_email
        from qtrusted.rows import UserRow

        @clauz3.guarantee(contracts.addresses_from(UserRow, "nosuch"))
        def main() -> None:
            send_email("x@example.com", "hi")
    """)
    assert [r.proof.conclusion for r in results] == [Conclusion.SKIP]
    assert "AttributeError" in str(results[0].proof.error)


def test_133_solver_rejected_encoding_is_a_skip() -> None:
    """A Z3Exception raised while solving a contract lands as SKIP.

    ``_solve_with`` re-raises it deliberately so ``Theorem.prove`` can label
    it; anything else would either escape or be misreported as a helper bug.
    """
    results = _prove("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import send_email

        @clauz3.guarantee(contracts.rejected_encoding())
        def main() -> None:
            send_email("x@example.com", "hi")
    """)
    assert [r.proof.conclusion for r in results] == [Conclusion.SKIP]
    assert results[0].proof.description == "solver rejected the encoding"
    assert "synthetic sort mismatch" in str(results[0].proof.error)


def test_133_contract_solve_exception_is_a_skip() -> None:
    """An ordinary exception from ContractSpec.solve is contained by _solve_with."""
    results = _prove("""
        import clauz3
        from qtrusted import contracts
        from qtrusted.effects import send_email

        @clauz3.guarantee(contracts.faulty_encoding())
        def main() -> None:
            send_email("x@example.com", "hi")
    """)
    assert [r.proof.conclusion for r in results] == [Conclusion.SKIP]
    assert "could not be encoded: ValueError" in str(results[0].proof.error)

from __future__ import annotations

import enum
import typing
from textwrap import dedent

import astroid
import z3

from clauz3.diagnostics import proof as proof_monitor

from ._annotations import ann2type
from ._cached_property import cached_property
from ._context import Context
from ._counterexamples import sequence_counterexample
from ._domains import add_value_domain
from ._eval_contracts import Contract, Contracts, eval_contracts
from ._eval_expr import eval_expr
from ._eval_stmt import eval_func
from ._exceptions import ProveError, UnsupportedError
from ._model import Model
from ._proxies import BoolSort


DEFAULT_TIMEOUT = 5.0
MAX_ASSUMPTION_CHECK_TIMEOUT = 1.0
MAX_QUANTIFIER_SCAN_NODES = 100_000


def _has_quantifier(expr: z3.ExprRef) -> bool:
    """Inspect a Z3 DAG once per node, with a fail-closed work bound."""
    pending = [expr]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        node_id = current.get_id()
        if node_id in seen:
            continue
        seen.add(node_id)
        if len(seen) > MAX_QUANTIFIER_SCAN_NODES:
            raise UnsupportedError('assumption expression is too large to inspect')
        if z3.is_quantifier(current):
            return True
        pending.extend(current.children())
    return False


class Conclusion(enum.Enum):
    OK = 'proved!'
    PARTIAL = 'partially proved'
    SKIP = 'skipped'
    VACUOUS = 'vacuous'
    FAIL = 'failed'


class Proof(typing.NamedTuple):
    conclusion: Conclusion
    description: str
    error: Exception | None = None
    example: Model | None = None

    @property
    def color(self) -> str:
        if self.conclusion == Conclusion.OK:
            return 'green'
        if self.conclusion in {Conclusion.FAIL, Conclusion.VACUOUS}:
            return 'red'
        return 'yellow'

    def evolve(self, **kwargs) -> Proof:
        return self._replace(**kwargs)

    def __str__(self) -> str:
        msg = f'{self.description}'
        if self.error:
            msg += f' ({self.error})'
        if self.example:
            msg += f'. Example: {self.example}.'
        return msg


class Constraint(typing.NamedTuple):
    condition: BoolSort
    description: str
    details: dict[str, object] | None = None


class Theorem:
    _func: astroid.FunctionDef

    def __init__(
        self, *,
        node: astroid.FunctionDef,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._func = node
        self._timeout = timeout

    @staticmethod
    def get_contracts(func: astroid.FunctionDef) -> typing.Iterator[Contract]:
        """The function should yield the information about contracts.

        Redefine this function for your needs.
        See tests/helpers.py for an example.
        """
        raise NotImplementedError

    @staticmethod
    def get_guarantees(func: astroid.FunctionDef) -> typing.Iterator[astroid.NodeNG]:
        return iter(())

    @classmethod
    def from_text(
        cls,
        content: str, *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> typing.Iterator[Theorem]:
        content = dedent(content)
        module = astroid.parse(content)
        yield from cls.from_astroid(module=module, timeout=timeout)

    @classmethod
    def from_astroid(
        cls, *,
        module: astroid.Module,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> typing.Iterator[Theorem]:
        for node in module.values():
            if isinstance(node, astroid.FunctionDef):
                yield cls(node=node, timeout=timeout)
                continue

            if isinstance(node, astroid.ClassDef):
                for subnode in node.body:
                    if not isinstance(subnode, astroid.FunctionDef):
                        continue
                    if not subnode.type == 'staticmethod':
                        continue
                    yield cls(node=subnode, timeout=timeout)

    @property
    def name(self) -> str:
        return self._func.name or 'unknown_function'

    @cached_property
    def _z3_context(self) -> z3.Context | None:
        # return z3.Context()
        return None

    @cached_property
    def _context(self) -> Context:
        ctx = Context.make_empty(
            get_contracts=self.get_contracts,
            get_guarantees=self.get_guarantees,
        )
        ctx = ctx.evolve(z3_ctx=self._z3_context)
        for name, value in self.arguments.items():
            ctx.scope.set(name=name, value=value)
            add_value_domain(value, ctx=ctx)
        return ctx

    @cached_property
    def arguments(self) -> dict[str, z3.SortRef]:
        result = dict()
        args: astroid.Arguments = self._func.args
        for arg, annotation in zip(args.args, args.annotations):
            if annotation is None:
                raise UnsupportedError('missed annotation for', arg.name)
            sort = ann2type(name=arg.name, node=annotation, ctx=self._z3_context)
            if sort is None:
                raise UnsupportedError('unsupported annotation type', annotation.as_string())
            result[arg.name] = sort
        return result

    @cached_property
    def _contracts(self) -> Contracts:
        # eval_func, not eval_stmt: #102 split the nested-declaration handler
        # off from body execution. Cached so the body is evaluated once — both
        # `assumptions` and `constraints` need it, and a second pass would
        # record every effect twice.
        try:
            eval_func(node=self._func, ctx=self._context)
        except UnsupportedError as exc:
            exc.phase = 'program body'
            node = getattr(exc, 'source_node', None)
            if node is not None:
                location = proof_monitor.location(node)
                position = (f"at line {location['line']}:" if 'line' in location
                            else f"in {location['source_kind']}:")
                try:
                    expression = node.as_string()[:300]
                except (ValueError, RecursionError):
                    # Diagnostic rendering must not replace the refusal with
                    # a crash (e.g. an integer beyond Python's repr limit).
                    expression = type(node).__name__
                exc.args += (position, expression)
            raise
        try:
            return eval_contracts(func=self._func, ctx=self._context, completed=True)
        except UnsupportedError as exc:
            exc.phase = 'function contract'
            raise

    @cached_property
    def assumptions(self) -> BoolSort:
        # Build this from the same frozen terms checked by
        # ``_check_assumptions``. Guarantee evaluation may add helper-local
        # facts to the context; allowing those later facts to change the
        # antecedent would make a guarantee able to justify itself.
        return BoolSort(expr=z3.And(*self._assumption_terms))

    @property
    def constraints(self) -> typing.Iterator[Constraint]:
        # Evaluation populates exceptions and assertions as a side effect.
        # Materialize it before inspecting either layer so direct consumers of
        # this public iterator cannot accidentally receive an empty theorem.
        contracts = self._contracts
        assumptions = self.assumptions
        for exc in self._context.exceptions:
            if exc.names & contracts.raises:
                continue
            descr = exc.name
            if exc.message:
                descr += ': {}'.format(exc.message)
            yield Constraint(
                description=descr,
                condition=self._imply(
                    given=assumptions,
                    expected=exc.cond,
                ),
            )
        for constraint in self._context.expected:
            yield Constraint(
                description='assertion',
                details=proof_monitor.details(constraint.expr),
                condition=self._imply(
                    given=assumptions,
                    expected=constraint.m_not(ctx=self._context),
                ),
            )
        for constraint in contracts.post:
            yield Constraint(
                description='post-condition',
                condition=self._imply(
                    given=assumptions,
                    expected=constraint.m_not(ctx=self._context),
                ),
            )
        try:
            for guarantee in self.get_guarantees(self._func):
                value = eval_expr(node=guarantee, ctx=self._context)
                # Do NOT fall back to `m_bool`. `ProxySort.m_bool` defaults to
                # true, so a guarantee that evaluates to a function, a string or
                # an integer would discharge itself and report `proved!`. Only a
                # proposition can be a guarantee.
                if not isinstance(value, BoolSort):
                    raise UnsupportedError(
                        'guarantee is not a boolean expression:',
                        guarantee.as_string(),
                    )
                constraint = value.m_bool(ctx=self._context)
                yield Constraint(
                    description='guarantee',
                    details=(
                        {**proof_monitor.location(guarantee), 'predicate': guarantee.as_string()[:300]}
                        if proof_monitor.active() else None
                    ),
                    condition=self._imply(
                        given=assumptions,
                        expected=constraint.m_not(ctx=self._context),
                    ),
                )
        except UnsupportedError as exc:
            exc.phase = 'guarantee expression'
            raise

    def _imply(self, given: BoolSort, expected: BoolSort) -> BoolSort:
        # A & (A => E) is A & E. Normalize the local bad-path condition before
        # attaching unrelated quantified domains; guarded indexing often
        # reduces to False through arithmetic normalization alone.
        local = z3.simplify(expected.expr, arith_lhs=True)
        expr = local if z3.is_false(local) else z3.And(given.expr, local)
        return BoolSort(expr=expr)

    def prove(self) -> Proof:
        try:
            self.arguments
        except UnsupportedError as exc:
            return Proof(
                conclusion=Conclusion.SKIP,
                description='failed to interpret function arguments',
                error=exc,
                example=None,
            )

        proofs = []
        try:
            assumptions_proof = self._check_assumptions()
            if assumptions_proof is not None:
                return assumptions_proof

            for constraint in self.constraints:
                solver = z3.Solver(ctx=self._z3_context)
                timeout_ms = proof_monitor.solver_timeout_ms(int(self._timeout * 1000))
                solver.set('timeout', timeout_ms)
                solver.add(constraint.condition.expr)
                proof_monitor.checking(constraint.description, constraint.details)
                proof = self._prove(solver=solver, descr=constraint.description, timeout=timeout_ms / 1000)
                proof_monitor.result(proof.conclusion.value, str(proof))
                if proof_monitor.active() and proof.conclusion != Conclusion.OK:
                    return proof
                if proof.conclusion == Conclusion.FAIL:
                    return proof
                proofs.append(proof)
        except UnsupportedError as exc:
            proofs.append(Proof(
                conclusion=Conclusion.SKIP,
                description='unsupported ' + getattr(exc, 'phase', 'proof construction'),
                error=exc,
                example=None,
            ))
        except z3.Z3Exception as exc:
            # Sort mismatches and similar encoding faults are prover
            # limitations, not proofs; report them the same way (#133).
            proofs.append(Proof(
                conclusion=Conclusion.SKIP,
                description='solver rejected the encoding',
                error=exc,
                example=None,
            ))
        for skip in self._context.skips:
            proofs.append(Proof(
                conclusion=Conclusion.SKIP,
                description='failed to interpret statement',
                error=skip,
                example=None,
            ))
        return self._select_proof(proofs=proofs)

    @cached_property
    def _assumption_terms(self) -> list[z3.BoolRef]:
        return [
            term.m_bool(ctx=self._context).expr
            for term in (*self._contracts.pre, *self._context.given)
        ]

    def _check_assumptions(self) -> Proof | None:
        """Reject a proof whose assumptions cannot hold, when that is provable.

        Every constraint is discharged as ``given -> expected``, so assumptions
        that cannot hold make each one vacuously valid and the whole theorem a
        no-op. ``VACUOUS`` is therefore returned only when unsatisfiability is
        *established*. If the quantifier-free check is undecided, the theorem
        fails closed with ``SKIP`` rather than allowing a possibly-vacuous
        obligation to report ``OK``.

        Check the quantifier-free subset first: it can establish a contradiction
        cheaply without quantified witnesses from set/list models. Then check
        the full frozen assumptions under the same short timeout. A value's
        quantified domain axiom can contradict an otherwise satisfiable trusted
        postcondition, even though the domain axiom alone is satisfiable.

        The full check only establishes vacuity on ``unsat``. Its ``unknown``
        result does not establish a contradiction and leaves ordinary obligation
        checking unchanged; otherwise satisfiable quantified set witnesses
        could prevent every such program from proving.
        """
        terms = self._assumption_terms
        if not terms:
            return None
        domain_ids = {term.expr.get_id() for term in self._context.pure_domains}
        if all(z3.is_true(term) or term.get_id() in domain_ids for term in terms):
            return None
        # Plain pre/postconditions can contradict generated domains. Their
        # presence requires the full check even without user quantifiers.
        quantified = [_has_quantifier(term) for term in terms]
        plain_terms = [
            term
            for term, has_quantifier in zip(terms, quantified, strict=True)
            if not has_quantifier
        ]

        solver = z3.Solver(ctx=self._z3_context)
        timeout = min(self._timeout, MAX_ASSUMPTION_CHECK_TIMEOUT)
        solver.set('timeout', proof_monitor.solver_timeout_ms(int(timeout * 1000)))
        if plain_terms:
            solver.add(z3.And(*plain_terms))
            result = solver.check()
            if result == z3.unknown:
                return Proof(
                    conclusion=Conclusion.SKIP,
                    description='failed to check assumptions',
                    error=ProveError(solver.reason_unknown()),
                    example=None,
                )
            if result == z3.unsat:
                return self._vacuous_proof()

        if not any(quantified):
            return None
        # Retain the plain conjuncts in this solver and add every remaining
        # frozen term, so this second query includes the complete antecedent.
        solver.add(*[
            term
            for term, has_quantifier in zip(terms, quantified, strict=True)
            if has_quantifier
        ])
        if proof_monitor.active():
            solver.set('timeout', proof_monitor.solver_timeout_ms(int(timeout * 1000)))
        if solver.check() != z3.unsat:
            return None
        return self._vacuous_proof()

    @staticmethod
    def _vacuous_proof() -> Proof:
        return Proof(
            conclusion=Conclusion.VACUOUS,
            description='assumptions are contradictory',
            error=None,
            example=None,
        )

    @staticmethod
    def _prove(
        solver: z3.Solver, descr: str, timeout: float = DEFAULT_TIMEOUT,
    ) -> Proof:
        result = solver.check()
        if result == z3.unsat:
            return Proof(
                conclusion=Conclusion.OK,
                description=descr,
                error=None,
                example=None,
            )
        if result == z3.unknown:
            model = sequence_counterexample(
                list(solver.assertions()),
                timeout_ms=proof_monitor.solver_timeout_ms(int(min(timeout, 1.0) * 1000)),
            )
            if model is not None:
                return Proof(
                    conclusion=Conclusion.FAIL,
                    description=descr,
                    error=None,
                    example=Model(model),
                )
            return Proof(
                conclusion=Conclusion.SKIP,
                description=descr,
                error=ProveError(solver.reason_unknown()),
                example=None,
            )
        if result == z3.sat:
            return Proof(
                conclusion=Conclusion.FAIL,
                description=descr,
                error=None,
                example=Model(solver.model()),
            )
        raise RuntimeError('unreachable')

    @staticmethod
    def _select_proof(proofs: list[Proof]) -> Proof:
        assert all(proof.conclusion != Conclusion.FAIL for proof in proofs)
        if not proofs:
            return Proof(
                conclusion=Conclusion.OK,
                description='nothing to prove',
            )
        some_ok = any(proof.conclusion == Conclusion.OK for proof in proofs)
        # if all proofs are skipped, just return the first one
        if not some_ok:
            return proofs[0]
        # if some proofs are skipped but some are ok, it is a partial proof
        for proof in proofs:
            if proof.conclusion == Conclusion.SKIP:
                return proof.evolve(conclusion=Conclusion.PARTIAL)
        # if all proofs are ok, all is good
        assert all(proof.conclusion == Conclusion.OK for proof in proofs)
        return Proof(
            conclusion=Conclusion.OK,
            description=', '.join(p.description for p in proofs),
            error=None,
            example=None,
        )

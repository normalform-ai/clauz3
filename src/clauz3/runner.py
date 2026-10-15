"""Runtime path for proved and approved clauz3 programs."""

import hashlib
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import astroid

from clauz3 import interp
from clauz3._vendor import deal_solver
from clauz3.approvals.client import (
    ApprovalResponse,
    ApprovalServiceError,
    configured_service_url,
    submit_approval_request,
)
from clauz3.approvals.receipts import (
    VERIFY_KEY_ENV,
    ReceiptError,
    consent_digest,
    verify_receipt,
    verifying_key,
)
from clauz3.diagnostics import proof as proof_monitor
from clauz3.guarantees import GuaranteeError, guarantee_texts
from clauz3.imports_policy import (
    DisallowedImportError,
    ImportPolicy,
    build_policy,
    validate_imports,
)
from clauz3.layer_state import LayerStateError
from clauz3.policy import compute_coverage, load_domain_policies
from clauz3.prover import (
    ProofResult,
    ProverConfigError,
    _selected_layer_context,
    _trusted_layer_context,
    prove_text,
    validate_untrusted_source,
)


@dataclass(frozen=True)
class RunOutcome:
    proofs: list[ProofResult]
    approval: ApprovalResponse
    request: dict[str, object]
    #: Trusted calls the program made, in order. Observable because the
    #: interpreter owns the one hand-off to CPython.
    effects: tuple[tuple[str, tuple[Any, ...], dict[str, Any]], ...] = ()


class RunConfigError(Exception):
    """The run configuration or source program is invalid."""


class RunProgramError(Exception):
    """A proved, approved program failed while running.

    Distinct from :class:`RunConfigError`: the source was valid and the proof
    covered it, but the program raised, failed an assertion, or exhausted the
    interpreter's step budget.
    """


class RunProofError(Exception):
    """The source program did not prove."""

    def __init__(self, proofs: list[ProofResult]) -> None:
        super().__init__("program did not prove")
        self.proofs = proofs


class ApprovalDeniedError(Exception):
    """The approval service did not approve execution."""

    def __init__(self, response: ApprovalResponse) -> None:
        super().__init__(response.decision)
        self.response = response


def discover_trusted_roots(*, import_roots: Iterable[Path]) -> list[Path]:
    """Discover default trusted roots such as tools/email/trusted.

    Import roots are taken literally, like everywhere else in the library API;
    pass ``expand_import_roots([project])`` to include the project's installed
    libraries under ``.clauz3/tools``.
    """

    roots: list[Path] = []
    seen: set[Path] = set()
    for import_root in import_roots:
        tools_dir = import_root / "tools"
        if not tools_dir.is_dir():
            continue
        for candidate in sorted(tools_dir.glob("*/trusted")):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            roots.append(candidate)
    return roots


def run_source(
    source: str,
    *,
    source_name: str,
    trusted_roots: Iterable[Path],
    import_roots: Iterable[Path],
    target: str = "main",
    approval_service_url: str | None = None,
    approval_timeout: float | None = None,
    extra_allowed_imports: Iterable[str] = (),
    step_budget: int = interp.DEFAULT_STEP_BUDGET,
) -> RunOutcome:
    """Prove, request approval for, and execute an inline program."""

    proof_monitor.phase("checking")
    import_root_list = [Path(path) for path in import_roots]
    trusted_root_list = [Path(path) for path in trusted_roots]
    extra_allowed_list = list(extra_allowed_imports)
    policy = build_policy(
        trusted_roots=trusted_root_list,
        import_roots=import_root_list,
        extra_allowed=extra_allowed_list,
    )
    # This source-only validation uses no selected module state. Keep its
    # established public ProverConfigError before translating loader errors.
    _validate_runtime_source(
        source=source, source_name=source_name, policy=policy, target=target
    )
    try:
        with _selected_layer_context(trusted_root_list, import_root_list) as layer:
            _preflight_structure(
                source=source,
                source_name=source_name,
                target=target,
                policy=policy,
                import_roots=import_root_list,
            )

            proof_monitor.phase("proving", description="constructing proof obligations")
            proofs = prove_text(
                source,
                target=target,
                import_paths=import_root_list,
                trusted_roots=trusted_root_list,
                source_name=source_name,
                extra_allowed_imports=extra_allowed_list,
            )
            if not proofs:
                raise RunConfigError(f"no theorem found for target {target!r}")
            if any(
                proof.proof.conclusion != deal_solver.Conclusion.OK for proof in proofs
            ):
                raise RunProofError(proofs)

            request = build_approval_request(
                source=source,
                source_name=source_name,
                target=target,
                proofs=proofs,
                trusted_roots=trusted_root_list,
                import_roots=import_root_list,
            )
    except ProverConfigError as exc:
        raise RunConfigError(_with_source(source_name, exc)) from exc
    service_url = approval_service_url or configured_service_url()
    if service_url is None:
        raise ApprovalServiceError(
            "no approval service configured; set CLAUZ3_APPROVAL_SERVICE",
        )
    proof_monitor.solver_timeout_ms(5000)
    proof_monitor.phase("submitting_approval", request_id=str(request["request_id"]))
    if approval_timeout is None:
        approval = submit_approval_request(service_url, request)
    else:
        approval = submit_approval_request(
            service_url,
            request,
            timeout=approval_timeout,
        )
    if not approval.approved:
        raise ApprovalDeniedError(approval)
    if not approval.receipt:
        raise ApprovalServiceError("approval response did not include a receipt")

    try:
        with layer.activate():
            _verify_receipt_or_refuse(approval=approval, source=source, request=request)

            proof_monitor.phase("executing")
            effects = _execute_source(
                source,
                source_name=source_name,
                target=target,
                import_roots=import_root_list,
                request_id=str(request["request_id"]),
                receipt=approval.receipt,
                policy=policy,
                step_budget=step_budget,
            )
    except LayerStateError as exc:
        raise RunConfigError(_with_source(source_name, exc)) from exc
    return RunOutcome(
        proofs=proofs,
        approval=approval,
        request=request,
        effects=effects,
    )


def _verify_receipt_or_refuse(
    *,
    approval: ApprovalResponse,
    source: str,
    request: Mapping[str, object],
) -> None:
    """Fail closed: refuse to run unless the receipt verifies for *this* run.

    Unlike a key that merely *enables* verification when set, the absence of a
    verifying key here is a hard refusal — otherwise an agent that can keep the
    key unset (the #69 threat model) disables the gate entirely. The receipt is
    checked against the exact source about to execute and the guarantees +
    coverage the human consented to, and its nonce is consumed for single-use.
    """

    verify_key = verifying_key()
    if verify_key is None:
        raise RunConfigError(
            "refusing to run: no approval verifying key provisioned. Set "
            f"{VERIFY_KEY_ENV} to the approval service's public key.",
        )
    assert approval.receipt is not None  # guarded by the caller
    program_sha256 = hashlib.sha256(source.encode()).hexdigest()
    consent = consent_digest(
        program_sha256=program_sha256,
        guarantees=cast(Sequence[str], request.get("guarantees", [])),
        coverage=request.get("coverage"),
    )
    nonce = verify_receipt(
        verify_key_b64=verify_key,
        receipt=approval.receipt,
        request_id=str(request["request_id"]),
        program_sha256=program_sha256,
        consent_sha256=consent,
        now=time.time(),
    )
    _consume_nonce(nonce)


def _consume_nonce(nonce: str) -> None:
    """Enforce single-use: reject a receipt nonce already recorded as consumed.

    Best-effort against concurrent local runs (check-then-append is not atomic);
    sufficient to stop straightforward replay of a captured receipt across
    separate ``clauz3 run`` invocations within the receipt's TTL.
    """

    store = _nonce_store()
    if store.exists() and nonce in store.read_text().splitlines():
        raise ReceiptError("approval receipt has already been used (replay)")
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a") as handle:
        handle.write(nonce + "\n")


def _nonce_store() -> Path:
    override = os.environ.get("CLAUZ3_CACHE")
    root = (
        Path(override).expanduser() if override else Path.home() / ".cache" / "clauz3"
    )
    return root / "consumed-receipts.txt"


def build_approval_request(
    *,
    source: str,
    source_name: str,
    target: str,
    proofs: list[ProofResult],
    trusted_roots: Iterable[Path],
    import_roots: Iterable[Path],
) -> dict[str, object]:
    trusted_roots = list(trusted_roots)
    import_roots = list(import_roots)
    with _trusted_layer_context(trusted_roots, import_roots):
        program_hash = hashlib.sha256(source.encode()).hexdigest()
        request_id = f"clr_{program_hash[:12]}"
        guarantees = _extract_guarantees(
            source=source,
            target=target,
            source_name=source_name,
        )
        policies = load_domain_policies(
            trusted_roots=trusted_roots,
            import_roots=import_roots,
        )
        return {
            "schema_version": 1,
            "kind": "clauz3.run",
            "request_id": request_id,
            "program_sha256": program_hash,
            "source_name": source_name,
            "target": target,
            "trusted_roots": [str(path) for path in trusted_roots],
            "import_roots": [str(path) for path in import_roots],
            "guarantees": guarantees,
            "coverage": compute_coverage(
                source=source,
                guarantees=guarantees,
                policies=policies,
                target=target,
                import_roots=import_roots,
                trusted_roots=trusted_roots,
            ),
            "proofs": [
                {
                    "name": result.name,
                    "conclusion": result.proof.conclusion.value,
                    "description": result.proof.description,
                }
                for result in proofs
            ],
            "program": source,
        }


def _extract_guarantees(*, source: str, target: str, source_name: str) -> list[str]:
    """The guarantees the human is asked to consent to.

    Shares one grammar checker with ``prove_text`` so the request can never
    carry a guarantee the prover would have refused -- nor silently drop one
    the prover read.
    """

    try:
        return guarantee_texts(source=source, target=target, source_name=source_name)
    except GuaranteeError as exc:
        raise RunConfigError(str(exc)) from exc


def _validate_runtime_source(
    *,
    source: str,
    source_name: str,
    policy: ImportPolicy,
    target: str = "main",
) -> None:
    """Pre-flight checks that are not the interpreter's job.

    Everything this used to do about *containment* is gone. The program is no
    longer handed to CPython, so there is nothing to fence off: no forbidden
    builtin list, no forbidden attribute list, no module-level validator, no
    AST rewrite. ``clauz3.interp`` cannot execute a construct outside the proved
    subset because it has no handler for one, and the soundness rules that used
    to live here -- module level is declarations only, assignment values and
    targets and defaults must be inert -- are enforced by ``_exec_module`` and
    ``_require_inert`` against the same tree the prover walked.

    What remains is genuinely prior to execution: unambiguous entry selection,
    the trusted-decorator gate (who may declare an effect), and the import
    allowlist. Both ``prove`` and ``run`` share these checks so an agent cannot
    slip past one into the other.
    """
    validate_untrusted_source(source=source, path=Path(source_name), target=target)
    try:
        validate_imports(source=source, source_name=source_name, policy=policy)
    except DisallowedImportError as exc:
        raise RunConfigError(str(exc)) from exc


def _preflight_structure(
    *,
    source: str,
    source_name: str,
    target: str,
    policy: ImportPolicy,
    import_roots: Iterable[Path],
) -> None:
    """Refuse what the interpreter would refuse, *before* the approval round trip.

    Moving the structural rules into ``interp.execute`` moved them to the end of
    the pipeline: after ``prove_text``, after the approval request, after the
    human decision, and after ``_verify_receipt_or_refuse`` consumed the
    single-use nonce. A program that proves and is then refused burns a receipt
    on a run that never happened -- and the deleted ``_validate_module_level``
    ran *before* proving. Two real cases::

        LIMIT = OTHER          # module level, non-constant: proves, then refused
        re.match("b", "bob")   # modelled by the prover, unmarshallable here

    This restores the ordering for the two classes that can be decided without
    running the program:

    1. **Module level and target resolution.** ``interp.target_node`` runs
       exactly the module body ``execute`` would -- under the same
       ``_require_inert`` gates, so nothing effectful can fire -- and resolves
       the target without calling it.
    2. **Subset vocabulary.** Every node in the tree must have a handler in one
       of the two registries. This is the same fail-closed rule ``execute``
       applies, hoisted to a static walk.

    What it deliberately does *not* do is re-implement the evaluator to
    pre-decide value-dependent refusals. ``re.match`` is an ``Attribute`` node
    with a handler; only resolving it against the module's surface refuses it,
    and doing that statically would mean a second interpreter -- the thing this
    design exists to avoid. That residue still surfaces post-approval. Closing
    it properly means teaching the prover to skip on
    ``LIBRARY_RETURNS_OUTSIDE_DOMAIN``, which is a prover change and not this
    one; ``docs/todos/subset-interpreter.md`` records it as open.
    """
    import_roots = list(import_roots)
    try:
        with _selected_layer_context(policy.trusted_roots, import_roots):
            try:
                module = astroid.parse(source, path=source_name)
            except astroid.AstroidSyntaxError as exc:
                raise RunConfigError(f"{source_name}: {exc}") from exc

            known = interp.eval_expr.node_types | interp.eval_stmt.node_types
            for node in module.body:
                for child in (node, *node.nodes_of_class(astroid.NodeNG)):
                    if type(child) in known or isinstance(child, _STRUCTURAL_NODES):
                        continue
                    raise RunConfigError(
                        f"{source_name}: {type(child).__name__} is not part of the "
                        "proved subset, so the program could not be run "
                        "even if it proved",
                    )

            try:
                # Static target resolution binds only trusted modules whose files were
                # loaded and checked under the configured roots. A dotted-name prefix
                # alone must not let the entry import a new module outside those roots.
                import_paths = list(import_roots)
                interp.validate_import_origins(
                    source,
                    source_name=source_name,
                    policy=policy,
                    import_roots=import_paths,
                )
                with _trusted_layer_context(list(policy.trusted_roots), import_paths):
                    interp.target_node(
                        source,
                        target=target,
                        source_name=source_name,
                        policy=policy,
                        import_roots=import_paths,
                    )
            except (interp.InterpError, ImportError, ProverConfigError) as exc:
                raise RunConfigError(_with_source(source_name, exc)) from exc
    except ProverConfigError as exc:
        raise RunConfigError(_with_source(source_name, exc)) from exc


def _with_source(source_name: str, exc: Exception) -> str:
    """Prefix the source name, unless the interpreter already did.

    Interpreter messages are inconsistent about it: anything built from
    ``frame.program.source_name`` carries it, while a ``ProgramError`` from a
    ``raise`` and a ``TrustedBoundaryError`` naming a qualname do not. Prefixing
    unconditionally produced ``<stdin>: <stdin>: ...``.
    """
    text = str(exc)
    if text.startswith(f"{source_name}:"):
        return text
    return f"{source_name}: {text}"


#: Tree scaffolding that is never dispatched on: astroid attaches these as
#: children of nodes whose handlers read them **directly**, so their absence
#: from the two registries is not a subset refusal.
#:
#: Every entry has to earn its place by naming the reader, because an entry
#: that does not is a hole in the gate rather than an exemption from it. The
#: first version listed ``AssignAttr``, ``DelName`` and ``ExceptHandler``,
#: which are children of constructs the subset rejects outright (``Delete``,
#: ``Try``) and which *no* handler reads: ``def main(): obj.attr = 1`` passed
#: the pre-flight and was refused only by ``_assign_to``'s catch-all, after the
#: approval round trip and after the nonce was consumed -- the exact residue
#: this function exists to remove.
#:
#: ``Starred`` goes the other way and was missing: ``_unpack`` and
#: ``_is_inert_target`` both read it deliberately, so ``A, *B = (1, 2, 3)``
#: ran under ``interp.execute`` and was refused by ``clauz3 run``.
_STRUCTURAL_NODES: tuple[type[astroid.NodeNG], ...] = (
    astroid.Module,  # the tree root
    astroid.Arguments,  # read by _bind_arguments / _eval_defaults
    astroid.AssignName,  # read by _assign_to
    astroid.Starred,  # read by _unpack and _is_inert_target
    astroid.Comprehension,  # read by the comprehension handlers
    astroid.Keyword,  # read by _call_arguments
    astroid.Decorators,  # read by _check_decorators
    astroid.Slice,  # read by _expr_subscript
)


def _execute_source(
    source: str,
    *,
    source_name: str,
    target: str,
    import_roots: Iterable[Path],
    request_id: str,
    receipt: str,
    policy: ImportPolicy,
    step_budget: int = interp.DEFAULT_STEP_BUDGET,
) -> tuple[tuple[str, tuple[Any, ...], dict[str, Any]], ...]:
    """Run a proved, approved program on the subset interpreter.

    This used to be ``exec(compile(module, source_name, "exec"), namespace)``
    against a filtered copy of ``builtins``. It now walks the same astroid tree
    the prover walked, over a closed value domain, so a construct outside the
    proved subset has no handler and cannot execute. See
    ``docs/todos/subset-interpreter.md``.

    Returns the trusted calls the program actually made, in order -- something
    the ``exec`` path could not observe at all.
    """
    with _temporary_environ(
        {
            "CLAUZ3_REQUEST_ID": request_id,
            "CLAUZ3_APPROVAL_RECEIPT": receipt,
        }
    ):
        try:
            outcome = interp.execute(
                source,
                source_name=source_name,
                policy=policy,
                target=target,
                import_roots=import_roots,
                step_budget=step_budget,
            )
        except interp.ProgramFault as exc:
            # Ordered before the base class on purpose. A proved, approved
            # program that raises, fails an assert, or runs out of step budget
            # is a *runtime* failure -- ``exec`` surfaced those as ordinary
            # Python exceptions and ``clauz3 run`` exited 1. Only a subset
            # violation means the source should not have been submitted.
            raise RunProgramError(_with_source(source_name, exc)) from exc
        except interp.InterpError as exc:
            raise RunConfigError(_with_source(source_name, exc)) from exc
        return outcome.effects


class _temporary_environ:
    def __init__(self, updates: Mapping[str, str]) -> None:
        self._updates = dict(updates)
        self._originals: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self._updates.items():
            self._originals[key] = os.environ.get(key)
            os.environ[key] = value

    def __exit__(self, *_exc_info: object) -> None:
        for key, original in self._originals.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original

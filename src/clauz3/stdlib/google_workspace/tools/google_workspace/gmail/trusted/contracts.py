"""Email send and draft policies for the assistant domain.

Recipient, count, uniqueness and content policies cover both ``send_email``
and ``create_draft``. ``drafts_only()`` forbids send calls. ``dry_run_only()``
constrains send calls to explicit previews; combine it with ``no_drafts()``
to forbid mailbox writes too. Each policy covers only this email domain.
"""

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Email = effect("send_email", "create_draft")
Send = effect("send_email")
Draft = effect("create_draft")


@contract(description="No restrictions are imposed on email tools.")
def no_guarantees() -> ContractSpec:
    """Impose no restrictions on email sending, previews, or draft creation.

    This is an explicit unrestricted email policy, not a read-only or preview
    policy. Other contracts in the same program still apply. It does not
    constrain effects from other libraries.
    """
    return core_no_guarantees()


@contract(
    description="Email send calls are previews only; saving drafts is still allowed."
)
def dry_run_only() -> ContractSpec:
    """Guarantee every send_email call passes ``dry_run=True`` explicitly.

    Discharges only when every call site passes the kwarg explicitly; agents that
    rely on the runtime default fail closed (the recorded effect fact
    does not include default kwarg values, so ``e.dry_run`` is an
    unknown field). This covers sending only; add ``no_drafts()`` to
    forbid saving drafts as well.
    """
    return Send.all(lambda e: e.dry_run == True)  # noqa: E712 -- symbolic equality


@contract(
    description=(
        "Email tools cannot send messages or preview sends; saving drafts is allowed."
    )
)
def no_emails() -> ContractSpec:
    """Forbid send_email calls, including dry runs; saved drafts are allowed.

    This has the same effect restriction as ``drafts_only()``. Add
    ``no_drafts()`` to forbid both send and draft calls. The name does not
    imply that all mailbox writes or other libraries' effects are forbidden.
    """
    return Send.empty()


@contract(
    description=(
        "Email tools address sent messages and saved drafts only to {allowlist}."
    )
)
def only_recipients(allowlist: list[str]) -> ContractSpec:
    """Restrict sent and draft recipient arguments to exact allowlisted strings.

    Every ``send_email`` (including previews) and ``create_draft`` call must
    pass a ``to`` value present in ``allowlist``. An empty list forbids both
    kinds of call. Matching is case-sensitive and does not parse address
    lists, resolve aliases, or normalize display names. Supply individual
    addresses as the allowed strings when restricting individual recipients.
    """
    return Email.all(lambda e: e.to in allowlist)


@contract(description="Email tools make at most one send or draft call per recipient.")
def unique_recipients() -> ContractSpec:
    """Allow at most one send or draft call per recipient argument per run.

    Uniqueness is checked across both effects on every reachable execution
    path, including send previews. Drafting and then sending to the same
    ``to`` string in one run violates this contract. Comparison uses exact
    strings, not resolved mailbox identities, and does not span separate runs.
    """
    return Email.distinct(lambda e: e.to)


@contract(description="Email tools make at most {count} send or draft calls in total.")
def emails_at_most(count: int) -> ContractSpec:
    """Cap the combined number of send_email and create_draft calls per run.

    Send previews count too, although they do not change the mailbox. Zero
    forbids both effects. This is an upper bound on calls, not a promise of
    successful delivery or draft creation; it does not limit other libraries.
    """
    return Email.count() <= count


@contract(description="Every sent message or saved draft has {marker} in its subject.")
def subject_must_contain(marker: str) -> ContractSpec:
    """Guarantee every subject contains ``marker``.

    Applies to sent emails and saved drafts. This checks text only; a
    ``[DRAFT]`` subject never makes sending behave like saving a draft.
    Use ``create_draft`` with ``drafts_only()`` for Gmail drafts.
    Matching is case-sensitive and includes previews. An empty marker adds
    no restriction. Other subject text remains unconstrained.
    """
    return Email.all(lambda e: marker in e.subject)


@contract(
    description=(
        "Every sent message or saved draft has at "
        "most {max_chars} characters in its subject."
    )
)
def subject_length_at_most(max_chars: int) -> ContractSpec:
    """Limit each sent or draft subject to ``max_chars`` Python characters.

    Includes send previews. The bound uses ``len(subject)``, not encoded
    bytes or the combined size of all messages. It does not restrict the
    number of messages, their recipients, or their bodies.
    """
    return Email.all(lambda e: len(e.subject) <= max_chars)


@contract(
    description=(
        "Every sent message or saved draft has "
        "at most {max_chars} characters in its body."
    )
)
def body_length_at_most(max_chars: int) -> ContractSpec:
    """Limit each sent or draft body to ``max_chars`` Python characters.

    Includes send previews. The bound uses ``len(body)``, not encoded bytes
    or the combined size of all messages. It does not restrict the number
    of messages, their recipients, or their subjects.
    """
    return Email.all(lambda e: len(e.body) <= max_chars)


@contract(
    description=(
        "No sent message or saved draft contains "
        "the literal text {substring} in its body."
    )
)
def no_body_contains(substring: str) -> ContractSpec:
    """Forbid the literal ``substring`` in sent and draft bodies, including previews.

    Matching is case-sensitive and applies to the original body argument.
    This excludes only the specified text, not encoded variants or arbitrary
    secrets. Subjects are unrestricted by this helper; use
    ``no_subject_contains`` to constrain them too.
    """
    return Email.all(lambda e: substring not in e.body)


@contract(
    description=(
        "No sent message or saved draft contains "
        "the literal text {substring} in its subject."
    )
)
def no_subject_contains(substring: str) -> ContractSpec:
    """Forbid the literal ``substring`` in sent and draft subjects, including previews.

    Matching is case-sensitive and applies to the original subject argument.
    This excludes only the specified text, not encoded variants or arbitrary
    secrets. Bodies are unrestricted by this helper; use ``no_body_contains``
    to constrain them too.
    """
    return Email.all(lambda e: substring not in e.subject)


@contract(
    description=(
        "Email tools may save drafts; sending "
        "messages and previewing sends are forbidden."
    )
)
def drafts_only() -> ContractSpec:
    """Allow saved drafts but forbid every send_email call, including dry runs.

    This covers the email library only; other libraries need their own
    guarantees. Use create_draft to save a real message in Gmail Drafts.
    """
    return Send.empty()


@contract(
    description="Email tools cannot save drafts; sending messages is still allowed."
)
def no_drafts() -> ContractSpec:
    """Forbid saving Gmail drafts through create_draft.

    Send calls remain allowed, including real sends. Combine with
    ``dry_run_only()`` for send previews with no mailbox changes through
    these email tools, or with ``no_emails()`` to forbid both effects.
    Effects from other libraries are not covered.
    """
    return Draft.empty()

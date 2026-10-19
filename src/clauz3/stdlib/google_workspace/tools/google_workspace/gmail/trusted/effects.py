"""Trusted email sending, previews, and Gmail draft creation through gog.

``create_draft`` saves an unsent message in Gmail. ``send_email`` is a no-op
by default and delivers a new message only with ``dry_run=False``; it does
not send an existing draft. The gog backend requires an authenticated Gmail
account. The legacy pa backend supports sending only.

``clauz3.requires`` declares argument checks, and ``clauz3.trusted`` marks
the effect boundary. Recipient, count, content, and delivery-mode guarantees
are supplied separately by this library's ``contracts`` module.
"""

import os
import subprocess

import deal

import clauz3

DEFAULT_BACKEND = "gog"


@clauz3.requires(
    lambda to, subject, body, *, dry_run=True: "@" in to,
    message='to must contain "@"',
)
@clauz3.requires(
    lambda to, subject, body, *, dry_run=True: len(subject) > 0,
    message="subject must be non-empty",
)
@deal.has(
    "net",
    "send",
    "syscall",
    "stdout",
    "stderr",
    "global",
    "import",
)
@clauz3.trusted
def send_email(
    to: str,
    subject: str,
    body: str,
    *,
    dry_run: bool = True,
) -> None:
    """Send immediately when ``dry_run=False``; never creates a saved draft.

    For a message in Gmail's Drafts folder, use ``create_draft`` instead.
    A ``[DRAFT]`` subject is only text, not a delivery mode.

    ``to`` is passed to the backend as its recipient argument and must
    contain "@"; ``subject`` must be nonempty. ``body`` is passed unchanged
    and may be empty. These checks do not validate that an address exists.

    When ``dry_run=True``, the body returns immediately and no subprocess
    is invoked. When ``dry_run=False``, the body shells out to
    ``gog gmail send`` (default) or another CLI selected by
    ``CLAUZ3_EMAIL_BACKEND``.

    The contract layer's ``dry_run_only()`` demands the kwarg be passed
    explicitly at every call site; relying on the default fails closed.
    A preview returns None without printing the proposed message. Print its
    content separately if it should be shown to the user. A real send also
    returns None; CLI output goes to the run log and CLI failures propagate.
    This creates a new message, rather than delivering or deleting an
    existing Gmail draft.
    """
    if dry_run:
        return
    backend = os.environ.get("CLAUZ3_EMAIL_BACKEND", DEFAULT_BACKEND)
    if backend == "gog":
        subprocess.run(
            ["gog", "gmail", "send", "--to", to, "--subject", subject, "--body", body],
            check=True,
        )
        return
    if backend == "pa":
        subprocess.run(
            ["pa", "email", "send", "--to", to, "-s", subject, "-b", body],
            check=True,
        )
        return
    raise ValueError(
        f"unsupported CLAUZ3_EMAIL_BACKEND={backend!r}; supported: 'gog', 'pa'"
    )


@clauz3.requires(lambda to, subject, body: "@" in to, message='to must contain "@"')
@clauz3.requires(
    lambda to, subject, body: len(subject) > 0,
    message="subject must be non-empty",
)
@deal.has(
    "net",
    "send",
    "syscall",
    "stdout",
    "stderr",
    "global",
    "import",
)
@clauz3.trusted
def create_draft(to: str, subject: str, body: str) -> None:
    """Save an unsent email in Gmail's Drafts folder; never sends the email.

    Use for requests to draft an email, including "send a draft". Pair with
    ``drafts_only()``, ``only_recipients([...])`` and ``emails_at_most(1)``.
    ``to`` must contain "@" and ``subject`` must be nonempty. ``body`` is
    passed unchanged and may be empty. No attachments are accepted.
    This is a real mailbox write, not a dry-run preview. The gog CLI reports
    the created draft's identifiers on stdout; this function returns None,
    not the draft ID. Errors propagate to the run.
    Only the gog backend supports drafts. Other backends fail before executing
    a command, without falling back to sending.
    """
    backend = os.environ.get("CLAUZ3_EMAIL_BACKEND", DEFAULT_BACKEND)
    if backend != "gog":
        raise ValueError(
            f"create_draft requires CLAUZ3_EMAIL_BACKEND='gog', got {backend!r}"
        )
    subprocess.run(
        [
            "gog",
            "gmail",
            "drafts",
            "create",
            "--no-input",
            "--to=" + to,
            "--subject=" + subject,
            "--body=" + body,
        ],
        check=True,
    )

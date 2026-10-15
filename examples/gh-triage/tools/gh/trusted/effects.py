"""Trusted effects for a GitHub triage agent.

These are the *real capabilities* the agent gets: it can read issue comments
and it can download release/attachment ZIPs. Nothing here is starved to make
the agent safe -- reading arbitrary issues and downloading archives are
exactly the things a useful triage/release agent must do. Safety comes from
the contracts in ``contracts.py`` that bound *provenance and data-flow*, not
from withholding tools.
"""

import deal
from tools.gh.trusted.schemas import Comment

import clauz3


@clauz3.trusted
@clauz3.ensures(lambda result: len(result) <= 100)
def list_comments(repo: str, issue: int, where: dict[str, object]) -> list[Comment]:
    """MOCK: return issue comments, filtered by the ``where`` clause.

    The prover never runs this body; it records a ``list_comments`` fact whose
    fields are ``repo``, ``issue``, and ``where``, and materializes a symbolic
    ``list[Comment]`` so a for-loop over the result can be reasoned about.
    """
    return []


@deal.has("network")
@clauz3.trusted
def download_zip(url: str) -> str:
    """MOCK: download the archive at ``url`` and return a local path.

    Real capability, deliberately unrestricted at the function level. Where it
    may point is a *contract* question, answered by ``urls_from``.
    """
    return ""

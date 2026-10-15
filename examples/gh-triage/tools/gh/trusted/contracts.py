"""Provenance and data-flow contracts for the GitHub triage agent.

The agent may read any issue and download any archive. These contracts do not
take capabilities away; they pin *where consumed data came from* and *where
acted-on values flow from*:

- ``comments_only_where`` constrains the **source**: every issue-comment query
  must carry a specific ``where`` filter (e.g. members only), so untrusted
  drive-by comments are never even read into the plan.
- ``urls_from`` constrains the **data-flow**: every download URL must be the
  ``asset_url`` *column of a comment row returned by a query* -- not a literal
  the agent typed, and not a value lifted out of an untrusted comment body.

Together they express: "only download archives whose URLs were posted, as
release assets, by repo members." Both are checked statically on every branch.
"""

from tools.gh.trusted.effects import download_zip, list_comments

from clauz3.spec import ContractSpec, contract, effect
from clauz3.spec import no_guarantees as core_no_guarantees

Comments = effect(list_comments)
Download = effect(download_zip)


@contract
def no_guarantees() -> ContractSpec:
    """Make no guarantees about GitHub effects."""
    return core_no_guarantees()


@contract
def comments_only_where(filter_dict: dict[str, object]) -> ContractSpec:
    """Guarantee every ``list_comments`` query uses exactly this ``where`` filter.

    This is a *source-provenance* contract: it fixes which comments are allowed
    to enter the plan at all.
    """
    return Comments.all(lambda e: e.where == filter_dict)


@contract
def urls_from(schema: type, field: str) -> ContractSpec:
    """Guarantee every download URL is ``schema``.``field`` of a queried comment row.

    This is a *data-flow* contract: the value handed to ``download_zip`` must be
    symbolically the given column of a ``list_comments`` result, not a literal
    or an unrelated value.
    """
    column = getattr(schema, field)
    return Download.all(lambda e: e.url == column)


@contract
def no_downloads() -> ContractSpec:
    """Guarantee the program downloads nothing."""
    return Download.empty()

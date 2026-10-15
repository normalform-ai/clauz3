# ruff: noqa: F821
#
# The legitimate task: triage issue #42, and for each release asset a repo
# MEMBER attached in the thread, download it (e.g. to reproduce a bug).
#
# The agent reads freely and downloads freely -- full expressivity. The user
# approves two provenance guarantees, not the code:
#   - every comment query is filtered to repo members, and
#   - every download URL is the asset_url column of one of those member
#     comments.
from tools.gh.trusted import contracts as gh
from tools.gh.trusted.effects import download_zip, list_comments
from tools.gh.trusted.schemas import Comment

import clauz3


@clauz3.guarantee(gh.comments_only_where({"author_association": "MEMBER"}))
@clauz3.guarantee(gh.urls_from(Comment, "asset_url"))
def main() -> None:
    for c in list_comments(
        "normalform-ai/clauz3", 42, where={"author_association": "MEMBER"}
    ):
        download_zip(c.asset_url)

import clauz3


class Comment(clauz3.Row):
    """One GitHub issue comment, as returned by ``list_comments``.

    ``author_association`` is GitHub's own trust signal for a comment's
    author relative to the repo: ``OWNER``, ``MEMBER``, ``COLLABORATOR``,
    ``CONTRIBUTOR``, ``NONE``, etc. ``asset_url`` is a release/attachment URL
    the comment points at, if any.
    """

    author: str
    author_association: str
    body: str
    asset_url: str

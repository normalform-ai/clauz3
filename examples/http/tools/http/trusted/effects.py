import clauz3
from clauz3._path_safety import url_has_no_parent_segments


@clauz3.requires(
    lambda url: url_has_no_parent_segments(url),
    message="url must not contain parent-directory segments",
)
@clauz3.requires(lambda url: len(url) > 0, message="url must be non-empty")
@clauz3.trusted
def http_get(url: str) -> str:
    """Trusted HTTP GET.

    Note: this is a mock function for testing; it won't *actually* make a
    request.

    Arguments:
      url: the URL to fetch. Must be non-empty.
    """
    return ""


@clauz3.requires(
    lambda url, body: url_has_no_parent_segments(url),
    message="url must not contain parent-directory segments",
)
@clauz3.requires(lambda url, body: len(url) > 0, message="url must be non-empty")
@clauz3.trusted
def http_post(url: str, body: str) -> None:
    """Trusted HTTP POST.

    Note: this is a mock function for testing; it won't *actually* make a
    request.

    Arguments:
      url: the URL to post to. Must be non-empty.
      body: the request body.
    """
    pass

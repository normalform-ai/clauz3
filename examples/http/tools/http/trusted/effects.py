import deal


@deal.pre(lambda url: len(url) > 0, message="url must be non-empty")
@deal.has("http", "trusted")
def http_get(url: str) -> str:
    """Trusted HTTP GET.

    Note: this is a mock function for testing; it won't *actually* make a
    request.

    Arguments:
      url: the URL to fetch. Must be non-empty.
    """
    return ""


@deal.pre(lambda url, body: len(url) > 0, message="url must be non-empty")
@deal.has("http", "trusted")
def http_post(url: str, body: str) -> None:
    """Trusted HTTP POST.

    Note: this is a mock function for testing; it won't *actually* make a
    request.

    Arguments:
      url: the URL to post to. Must be non-empty.
      body: the request body.
    """
    pass

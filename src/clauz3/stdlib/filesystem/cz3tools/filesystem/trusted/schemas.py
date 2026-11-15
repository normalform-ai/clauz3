import clauz3


class Match(clauz3.Row):
    """One matching line, as returned by ``search_files``.

    ``path`` is the file's path as ripgrep reports it under the searched
    ``path`` argument. ``line_number`` is one-based. ``text`` is the line
    without its terminator.
    """

    path: str
    line_number: int
    text: str

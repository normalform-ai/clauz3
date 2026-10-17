"""Predicates shared by trusted effects that accept path-like strings."""


def has_no_parent_segments(path: str) -> bool:
    """Return whether ``path`` contains no lexical ``..`` component.

    POSIX separators, Windows separators, and the Windows drive delimiter are
    all treated as component boundaries, including in mixed-separator paths.
    The drive delimiter matters because Windows reads ``C:..`` as a
    drive-relative parent reference even though no separator precedes the dots.

    This is deliberately lexical: trusted effects can use it in ``deal``
    preconditions that the symbolic evaluator can prove before the effect runs.
    """
    # A common agent guard establishes this stronger condition directly.
    # Keep it explicit so proving the implication does not require the solver
    # to rediscover ten substring/prefix/suffix relationships. The detailed
    # branch still accepts harmless embedded dots such as "report..txt".
    return ".." not in path or (
        path != ".."
        and not path.startswith("../")
        and not path.startswith("..\\")
        and "/../" not in path
        and "/..\\" not in path
        and "\\../" not in path
        and "\\..\\" not in path
        and ":.." not in path
        and not path.endswith("/..")
        and not path.endswith("\\..")
    )


def has_plain_segments(path: str) -> bool:
    r"""Return whether ``path`` has no backslash and no empty or ``.`` component
    after the first.

    ``/repo//PRECIOUS.md`` and ``/home/user/./.ssh`` name the same files as
    ``/repo/PRECIOUS.md`` and ``/home/user/.ssh`` but do not start with them, so
    a lexical deny-list prefix would miss them. A leading ``./`` and a bare
    ``.`` are allowed: they are how relative paths are commonly written, and
    ``find`` returns them. Contracts compare both spellings.

    Separators are POSIX only. A backslash is refused outright, because on
    Windows it is a separator and ``C:\repo\.\x`` would step around the same
    prefixes.

    >>> [has_plain_segments(p) for p in ("./data/a", ".", "/a/.env", "a/b/")]
    [True, True, True, True]
    >>> [has_plain_segments(p) for p in ("/a//b", "/a/./b", "/a/.", ".//a", "a\\b")]
    [False, False, False, False, False]
    """
    # As in ``has_no_parent_segments``, the first disjunct is a guard an agent
    # can establish directly; it is stronger than needed.
    return (
        "//" not in path
        and "\\" not in path
        and ("/." not in path or ("/./" not in path and not path.endswith("/.")))
    )


def is_plain_path(path: str) -> bool:
    """Return whether ``path`` has no ``..``, ``.`` or empty segments, or backslashes.

    The precondition every filesystem effect states on its path arguments.

    >>> is_plain_path("./data/a"), is_plain_path("/a/../b"), is_plain_path("/a//b")
    (True, False, False)
    """
    return has_no_parent_segments(path) and has_plain_segments(path)


def url_has_no_parent_segments(url: str) -> bool:
    """Return whether a URL has no literal or percent-encoded dot segments.

    Rejecting encoded dots prevents a server-side URL decoder from turning an
    apparently in-prefix URL into a parent traversal after proof.

    A traversal needs a ``..`` segment bounded on both sides. Reasoning only
    about the closing side is not enough: ``%2f../escape`` decodes to
    ``//../escape``, where the opening boundary is the encoded separator.

    Rather than enumerate boundary positions, refuse the encodings themselves.
    A traversal that survives any number of standard ``unquote`` passes must
    end up spelling a ``..`` segment out of the characters ``.``, ``/`` and
    ``\\``. Each of those reaches the final string either literally — caught by
    ``has_no_parent_segments`` — or by decoding one of:

    - ``%2e`` — encoded dot
    - ``%2f``, ``%5c`` — encoded separator, on either side of a dot segment

    For such a token to appear at a decode depth greater than one it must be
    *synthesised* from a shallower encoding, and every synthesis introduces a
    member of a small closed set. A ``%`` can come only from ``%25``, and the
    hex digits spelling ``2e``/``2f``/``5c`` — ``2``, ``5``, ``c``, ``e``,
    ``f`` — only from their own encodings. Refusing those blocks the
    construction at every depth, because assembling a token without them
    requires it to be present literally, where the direct checks catch it:

    - ``%25`` — encoded percent, which synthesises a ``%``
    - ``%32`` (``2``), ``%35`` (``5``), ``%63``/``%43`` (``c``/``C``),
      ``%65``/``%45`` (``e``/``E``), ``%66``/``%46`` (``f``/``F``)

    Two positional markers close the remaining literal cases:

    - ``..%`` — literal dots meeting an encoding, so a separator can still
      arrive from the decode
    - ``..?``, ``..#`` — a trailing ``..`` segment closed by the query or
      fragment marker rather than by a separator

    This is deliberately stricter than a URL parser: a legitimate query string
    containing any of those tokens is refused rather than analysed. Encodings
    whose decoded bytes cannot spell a token — ``%20``, ``%2C`` — still pass.
    Decoders that invent non-standard separator spellings (overlong UTF-8,
    ``;`` path parameters) are out of scope; the guarantee is against repeated,
    standards-compliant percent-decoding.
    """
    return (
        has_no_parent_segments(url)
        # Depth-one traversal tokens: encoded dot and encoded separators.
        and "%2e" not in url
        and "%2E" not in url
        and "%2f" not in url
        and "%2F" not in url
        and "%5c" not in url
        and "%5C" not in url
        # The percent synthesiser, which is what enables deeper encodings.
        and "%25" not in url
        # Encoded hex digits that could spell a token above on a later pass.
        and "%32" not in url
        and "%35" not in url
        and "%43" not in url
        and "%63" not in url
        and "%45" not in url
        and "%65" not in url
        and "%46" not in url
        and "%66" not in url
        # Literal dots meeting an encoding or the query/fragment terminator.
        and "..%" not in url
        and "..?" not in url
        and "..#" not in url
    )


def is_single_path_component(name: str) -> bool:
    """Return whether ``name`` is one path component and not a dot segment.

    For arguments that are interpolated into a filename inside an
    already-validated directory (``root / f"{name}.csv"``), checking for ``..``
    is not enough: a separator alone reaches a subdirectory, and an absolute or
    drive-qualified value replaces the root entirely, because ``Path("/a") /
    "/b"`` is ``/b`` and ``PureWindowsPath("C:/a") / "D:b"`` is ``D:b``.
    Requiring a single component rules all of them out.
    """
    return (
        "/" not in name
        and "\\" not in name
        and ":" not in name
        and name != "."
        and name != ".."
    )

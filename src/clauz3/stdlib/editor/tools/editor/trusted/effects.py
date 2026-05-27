"""Trusted editor effects: in-place file modification.

Both ``edit_file`` (full-content replace) and ``append_file`` are real
non-mock implementations. They both carry the ``edit`` marker so a
single ``Edit = effect("edit")`` relation in the contract layer covers
all in-place mutation.

This is intentionally a separate stdlib tool from ``filesystem``:

- the ``edit`` marker is distinct from ``write``, so a project can
  permit reads + edits while still forbidding from-scratch writes
  (or vice versa);
- ``append_file`` is genuinely a different operation from ``write_file``
  (preserves prior content), so the contract layer can talk about both
  shapes;
- the content-substring contracts (``must_not_write_text``) are unique
  to the editor surface — useful as exfil / secrets / banned-token guards
  before any text reaches disk.
"""

from pathlib import Path

import deal


@deal.pre(lambda path, new_text: len(path) > 0, message="path must be non-empty")
@deal.has("edit", "write", "trusted")
def edit_file(path: str, new_text: str) -> None:
    """Replace the UTF-8 contents of ``path`` with ``new_text``.

    Creates parent directories as needed; overwrites any existing file.
    Recorded under the ``edit`` marker.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_text, encoding="utf-8")


@deal.pre(lambda path, text: len(path) > 0, message="path must be non-empty")
@deal.has("edit", "write", "trusted")
def append_file(path: str, text: str) -> None:
    """Append ``text`` to the UTF-8 contents of ``path``.

    Creates parent directories and the file as needed. Recorded under
    the ``edit`` marker, with field name ``text`` (not ``new_text``).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(text)

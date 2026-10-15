"""Trusted editor effects: in-place file modification.

Both ``edit_file`` (full-content replace) and ``append_file`` are real
non-mock implementations. The contract layer names them together ---
``Edit = effect("edit_file", "append_file")`` --- so one relation covers all
in-place mutation.

This is intentionally a separate stdlib tool from ``filesystem``:

- an edit is a distinct effect from a write, so a project can permit reads +
  edits while still forbidding from-scratch writes (or vice versa);
- ``append_file`` is genuinely a different operation from ``write_file``
  (preserves prior content), so the contract layer can talk about both
  shapes;
- the content-substring contracts (``must_not_write_text``) are unique
  to the editor surface — useful as exfil / secrets / banned-token guards
  before any text reaches disk.
"""

from pathlib import Path

import deal

import clauz3
from clauz3._path_safety import has_no_parent_segments


@clauz3.requires(
    lambda path, new_text: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(lambda path, new_text: len(path) > 0, message="path must be non-empty")
@deal.has("write")
@clauz3.trusted
def edit_file(path: str, new_text: str) -> None:
    """Replace the UTF-8 contents of ``path`` with ``new_text``.

    Creates parent directories as needed; overwrites any existing file.
    Covered by the ``Edit`` relation, along with ``append_file``.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_text, encoding="utf-8")


@clauz3.requires(
    lambda path, text: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(lambda path, text: len(path) > 0, message="path must be non-empty")
@deal.has("write")
@clauz3.trusted
def append_file(path: str, text: str) -> None:
    """Append ``text`` to the UTF-8 contents of ``path``.

    Creates parent directories and the file as needed. Recorded under
    the ``Edit`` relation, with field name ``text`` (not ``new_text``).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(text)

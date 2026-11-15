"""What filesystem tools do to the filesystem.

Every effect in ``effects.py`` declares the events it performs, and the
contracts in ``contracts.py`` are stated over these events, so a policy covers
every tool that performs the event. ``copy`` reads its source and writes its
destination, so ``never_read_under`` sees the copy and ``only_write_under`` sees
the destination.

The classes form a hierarchy, and a relation over a class covers its
subclasses::

    FsEvent(path)
    ├── Observe          anything that reveals filesystem state
    │   ├── Read         file contents
    │   │   └── RecursiveRead   copy/move source, possibly a whole directory
    │   └── List         names under a directory
    └── Write            any modification
        ├── WriteText(text)   text written to a file
        ├── Create            a new directory or empty file
        │   └── DirectoryCreate  explicit directory creation
        ├── RecursiveWrite    modification that may cover descendants
        │   └── Delete        removal (move removes its source)
        └── Chmod(mode)       permission bits

A path may name a directory, and copy, move and remove act on everything under
one. Contracts that forbid a prefix therefore refuse an event whose path is a
prefix of it too: ``remove("/repo")`` deletes ``/repo/PRECIOUS``.
"""

from clauz3.events import Event


class FsEvent(Event):
    """An event on the filesystem at ``path``."""

    path: str


class Observe(FsEvent):
    """Filesystem state is revealed to the program."""


class Read(Observe):
    """The contents at ``path`` are read."""


class RecursiveRead(Read):
    """Read a path that may be a directory and all its descendants."""


class List(Observe):
    """The names under the directory ``path`` are listed."""


class Write(FsEvent):
    """The filesystem at ``path`` is modified."""


class WriteText(Write):
    """``text`` is written to the file at ``path``, replacing or appending."""

    text: str


class Create(Write):
    """A directory or empty file is created at ``path``."""


class DirectoryCreate(Create):
    """Explicitly create a directory, with parents."""


class RecursiveWrite(Write):
    """Modify a path that may cover a directory and its descendants."""


class Delete(RecursiveWrite):
    """``path``, and everything under it, is removed."""


class Chmod(Write):
    """The permission bits of ``path`` are set to ``mode``."""

    mode: int

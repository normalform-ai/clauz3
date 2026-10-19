"""Typed lexical paths for contract arguments, without filesystem access."""

from dataclasses import dataclass

from clauz3._path_safety import is_plain_path


def _validate(value: str) -> None:
    if type(value) is not str:
        raise TypeError("a path must be a string")
    if not value or "\x00" in value or ":" in value or not is_plain_path(value):
        raise ValueError("a path must be nonempty with plain POSIX path segments")


@dataclass(frozen=True)
class FilePath:
    """An exact file name, not a prefix or a check that the file exists."""

    value: str

    def __post_init__(self) -> None:
        _validate(self.value)
        if self.value == "." or self.value.endswith("/"):
            raise ValueError("a file path must name a file, not a directory")


@dataclass(frozen=True)
class DirectoryPath:
    """A directory boundary, not a check of existence or symlink resolution."""

    value: str

    def __post_init__(self) -> None:
        _validate(self.value)

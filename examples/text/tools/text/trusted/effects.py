import clauz3
from clauz3._path_safety import has_no_parent_segments


@clauz3.requires(lambda channel, text: len(text) > 0, message="text must be non-empty")
@clauz3.trusted
def send_message(channel: str, text: str) -> None:
    """Trusted text sender.

    Note: this is a mock function for testing; it won't *actually* post anything.

    Arguments:
      channel: where the text is posted.
      text: the body to send. Must be non-empty.
    """
    pass


@clauz3.requires(
    lambda path, new_text: has_no_parent_segments(path),
    message="path must not contain parent-directory segments",
)
@clauz3.requires(lambda path, new_text: len(path) > 0, message="path must be non-empty")
@clauz3.trusted
def edit_file(path: str, new_text: str) -> None:
    """Trusted file editor.

    Note: this is a mock function for testing; it won't *actually* edit a file.

    Arguments:
      path: file whose contents are replaced. Must be non-empty.
      new_text: the replacement contents.
    """
    pass

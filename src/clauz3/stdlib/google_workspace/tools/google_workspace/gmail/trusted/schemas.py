import clauz3


class Message(clauz3.Row):
    """One Gmail message, as returned by ``search_messages``.

    ``sender`` is the raw ``From`` header, for example
    ``"Alice Example" <alice@example.com>``; ``sender_address`` is the bare
    address parsed from it, ``alice@example.com``, or empty if unparseable.
    Recipients (To/Cc) are not included.
    ``date`` is gog's local-time ``YYYY-MM-DD HH:MM`` rendering. ``labels``
    joins Gmail label IDs with commas, such as ``INBOX,UNREAD``. ``body`` is
    the decoded message text, and may be empty for messages without a text part.
    """

    id: str
    thread_id: str
    date: str
    sender: str
    sender_address: str
    subject: str
    labels: str
    body: str

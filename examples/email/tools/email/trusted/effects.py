import clauz3


@clauz3.requires(lambda addr, msg: "@" in addr, message='addr must contain "@"')
@clauz3.trusted
@clauz3.mock
def send_email(addr: str, msg: str) -> None:
    """trusted email sender.

    Note: this is a mock function for testing, it won't *actually* send an email

    Arguments:
      addr: recipient. Must be a valid email address
      msg: body of email
    """
    pass

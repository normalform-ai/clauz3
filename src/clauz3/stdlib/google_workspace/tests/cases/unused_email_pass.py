"""A selected email library must not block a program that never imports it."""


def main() -> int:
    lines = ["first line", "second line"]
    assert len(lines) == 2
    return len(lines)

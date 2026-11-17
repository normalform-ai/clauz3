"""An allowed URL prefix must name a whole host, not the start of one."""

from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import ProofResult, prove_path

WEB_ROOT = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib/web_fetch"
WEB_TRUSTED = WEB_ROOT / "tools/web_fetch/trusted"


def _prove(tmp_path: Path, guarantee: str, url: str) -> list[ProofResult]:
    case = tmp_path / "case.py"
    case.write_text(
        "import clauz3\n"
        "from tools.web_fetch.trusted import contracts as web\n"
        "from tools.web_fetch.trusted.effects import fetch_url\n"
        "\n"
        f"@clauz3.guarantee(web.{guarantee})\n"
        "def main() -> None:\n"
        f"    fetch_url({url!r})\n"
    )
    return prove_path(case, trusted_roots=[WEB_TRUSTED], import_roots=[WEB_ROOT])


@pytest.mark.parametrize(
    ("prefix", "url"),
    [
        ("https://api.github.com", "https://api.github.com.evil.example/x"),
        ("https://api.github.com", "https://api.github.com@evil.example/x"),
        ("https://api.github.com", "https://api.github.com/repos/x/y"),
        ("https://api.github.com:443", "https://api.github.com:4430/x"),
        ("https://api.git", "https://api.github.com/x"),
        ("http://localhost", "http://localhost.evil.example/x"),
        ("https://api.github.com?", "https://api.github.com?x=1"),
    ],
)
def test_an_allowed_prefix_that_stops_inside_the_host_is_refused(
    tmp_path: Path, prefix: str, url: str
) -> None:
    """Otherwise the guarantee would hold for a different site than it names."""
    results = _prove(tmp_path, f"only_fetch_under({prefix!r})", url)
    # The guarantee is refused; the program's other obligations still prove.
    assert [r.proof.conclusion for r in results] == [Conclusion.PARTIAL]
    assert "must end its host name with '/'" in str(results[0].proof.error)


@pytest.mark.parametrize(
    ("prefix", "url", "expected"),
    [
        ("https://api.github.com/", "https://api.github.com/x", Conclusion.OK),
        ("https://api.github.com/repos/", "https://api.github.com/x", Conclusion.FAIL),
        (
            "https://api.github.com/",
            "https://api.github.com.evil.example/x",
            Conclusion.FAIL,
        ),
        ("https://api.github.com:443/", "https://api.github.com:443/x", Conclusion.OK),
        # A bare scheme names no host and admits any, as its description says.
        ("https://", "https://anything.example/x", Conclusion.OK),
        ("https://", "http://anything.example/x", Conclusion.FAIL),
    ],
)
def test_an_allowed_prefix_with_a_whole_host_is_checked(
    tmp_path: Path, prefix: str, url: str, expected: Conclusion
) -> None:
    results = _prove(tmp_path, f"only_fetch_under({prefix!r})", url)
    assert [r.proof.conclusion for r in results] == [expected]


@pytest.mark.parametrize(
    "url",
    [
        "https://internal.corp/x",
        "https://internal.corp:8443/x",
        "https://internal.corp.example/x",
    ],
)
def test_a_denied_host_prefix_is_not_refused(tmp_path: Path, url: str) -> None:
    """A host-only deny prefix blocks more addresses, so it stays usable."""
    results = _prove(tmp_path, 'never_fetch_under("https://internal.corp")', url)
    assert [r.proof.conclusion for r in results] == [Conclusion.FAIL]

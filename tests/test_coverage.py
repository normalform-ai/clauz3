from pathlib import Path

import pytest

from clauz3.policy import (
    DomainPolicy,
    compute_coverage,
    load_domain_policies,
)

ROOT = Path(__file__).resolve().parents[1]
EMAIL_ROOT = ROOT / "examples/email"
TRUSTED_ROOT = EMAIL_ROOT / "tools/email/trusted"

EMAIL_POLICY = DomainPolicy(
    when_used=("send_email",),
    recommended=("only", "unique_recipients"),
    domain_package="tools.email.trusted",
    label="email",
)


def _source(*guarantee_calls: str) -> str:
    decorators = "\n".join(f"@clauz3.guarantee({call})" for call in guarantee_calls)
    return f"""
import clauz3
from tools.email.trusted import contracts as emails
from tools.email.trusted.effects import send_email


{decorators}
def main() -> None:
    send_email("bob@example.com", "Report is ready")
"""


def _email_entry(report: list[dict[str, object]]) -> dict[str, object]:
    (entry,) = [row for row in report if row["domain"] == "email"]
    return entry


def test_covered_when_all_recommended_present() -> None:
    source = _source(
        "emails.only(['bob@example.com'])",
        "emails.unique_recipients()",
    )
    report = compute_coverage(
        source=source,
        guarantees=["emails.only(['bob@example.com'])", "emails.unique_recipients()"],
        policies=[EMAIL_POLICY],
    )
    assert _email_entry(report)["status"] == "covered"


def test_recommended_gap_when_domain_addressed_but_recommendation_missing() -> None:
    source = _source("emails.only(['bob@example.com'])")
    entry = _email_entry(
        compute_coverage(
            source=source,
            guarantees=["emails.only(['bob@example.com'])"],
            policies=[EMAIL_POLICY],
        )
    )
    assert entry["status"] == "recommended_gap"
    assert entry["missing_recommended"] == ["unique_recipients"]


def test_silent_gap_when_domain_used_but_unaddressed() -> None:
    source = _source()  # uses send_email, states nothing
    entry = _email_entry(
        compute_coverage(source=source, guarantees=[], policies=[EMAIL_POLICY])
    )
    assert entry["status"] == "silent_gap"


def test_unused_domain_is_omitted() -> None:
    source = """
def main() -> None:
    pass
"""
    assert compute_coverage(source=source, guarantees=[], policies=[EMAIL_POLICY]) == []


def test_missing_default_target_is_not_reported_as_an_unused_domain() -> None:
    source = _source().replace("def main()", "def task()")
    with pytest.raises(ValueError, match=r"target 'main'.*module-level") as exc_info:
        compute_coverage(source=source, guarantees=[], policies=[EMAIL_POLICY])
    assert "target=" in str(exc_info.value)


def test_missing_explicit_target_is_refused() -> None:
    with pytest.raises(ValueError, match=r"target 'missing'.*module-level"):
        compute_coverage(
            source=_source(),
            guarantees=[],
            policies=[EMAIL_POLICY],
            target="missing",
        )


@pytest.mark.parametrize(
    "source",
    [
        "class task:\n    pass\n",
        "class Ops:\n    def task() -> None:\n        send_email()\n",
        "def outer() -> None:\n    def task() -> None:\n        send_email()\n",
    ],
    ids=["class", "method", "nested-function"],
)
def test_target_must_be_a_module_level_function(source: str) -> None:
    with pytest.raises(ValueError, match=r"target 'task'.*module-level"):
        compute_coverage(
            source=source, guarantees=[], policies=[EMAIL_POLICY], target="task"
        )


def test_explicit_custom_target_preserves_guarantee_domain_resolution() -> None:
    guarantees = ["emails.only(['bob@example.com'])", "emails.unique_recipients()"]
    source = _source(*guarantees).replace("def main()", "def task()")
    report = compute_coverage(
        source=source, guarantees=guarantees, policies=[EMAIL_POLICY], target="task"
    )
    assert report == [
        {
            "domain": "email",
            "status": "covered",
            "missing_required": [],
            "missing_recommended": [],
        }
    ]


def test_required_gap_takes_precedence() -> None:
    policy = DomainPolicy(
        when_used=("send_email",),
        required=("only",),
        recommended=("unique_recipients",),
        domain_package="tools.email.trusted",
    )
    entry = compute_coverage(source=_source(), guarantees=[], policies=[policy])[0]
    assert entry["status"] == "required_gap"
    assert entry["missing_required"] == ["only"]


def _two_domain_source(*guarantee_calls: str) -> str:
    return _source(*guarantee_calls).replace(
        "from tools.email.trusted.effects import send_email\n",
        "from tools.email.trusted.effects import send_email\n"
        "from tools.fs.trusted import contracts as fs\n",
    )


def test_another_domains_same_named_contract_is_not_the_required_one() -> None:
    """``fs.only(...)`` says nothing about who may be emailed."""
    policy = DomainPolicy(
        when_used=("send_email",),
        required=("only",),
        recommended=("unique_recipients",),
        domain_package="tools.email.trusted",
        label="email",
    )
    guarantees = ["fs.only(['/tmp'])", "emails.unique_recipients()"]
    entry = _email_entry(
        compute_coverage(
            source=_two_domain_source(*guarantees),
            guarantees=guarantees,
            policies=[policy],
        )
    )
    assert entry == {
        "domain": "email",
        "status": "required_gap",
        "missing_required": ["only"],
        "missing_recommended": [],
    }


def test_another_domains_same_named_contract_is_not_a_recommended_one() -> None:
    guarantees = ["fs.only(['/tmp'])", "emails.unique_recipients()"]
    entry = _email_entry(
        compute_coverage(
            source=_two_domain_source(*guarantees),
            guarantees=guarantees,
            policies=[EMAIL_POLICY],
        )
    )
    assert entry["status"] == "recommended_gap"
    assert entry["missing_recommended"] == ["only"]


def test_a_contract_in_a_submodule_of_the_domain_package_counts() -> None:
    policy = DomainPolicy(
        when_used=("send_email",),
        required=("only",),
        domain_package="tools.email",
        label="email",
    )
    guarantees = ["emails.only(['bob@example.com'])"]
    entry = _email_entry(
        compute_coverage(
            source=_source(*guarantees), guarantees=guarantees, policies=[policy]
        )
    )
    assert entry["status"] == "covered"


def test_a_sibling_package_sharing_a_prefix_is_another_domain() -> None:
    policy = DomainPolicy(
        when_used=("send_email",),
        required=("only",),
        domain_package="tools.email.trust",
        label="email",
    )
    guarantees = ["emails.only(['bob@example.com'])"]
    entry = _email_entry(
        compute_coverage(
            source=_source(*guarantees), guarantees=guarantees, policies=[policy]
        )
    )
    assert entry["missing_required"] == ["only"]


def test_a_policy_without_a_domain_package_still_matches_by_name() -> None:
    """A hand-built policy names no package, so there is none to match against."""
    policy = DomainPolicy(
        when_used=("send_email",),
        required=("only",),
        recommended=("unique_recipients",),
        label="email",
    )
    guarantees = ["emails.only(['bob@example.com'])"]
    entry = _email_entry(
        compute_coverage(
            source=_source(*guarantees), guarantees=guarantees, policies=[policy]
        )
    )
    assert entry["missing_required"] == []
    assert entry["missing_recommended"] == ["unique_recipients"]


def test_load_domain_policies_from_example() -> None:
    policies = load_domain_policies(
        trusted_roots=[TRUSTED_ROOT],
        import_roots=[EMAIL_ROOT],
    )
    assert len(policies) == 1
    policy = policies[0]
    assert policy.when_used == ("send_email",)
    assert policy.recommended == ("only", "unique_recipients")
    assert policy.domain_package == "tools.email.trusted"

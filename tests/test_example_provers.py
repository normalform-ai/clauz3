"""Pin exact conclusions and discharged obligations for five worked examples.

A failed guarantee must be a refutation, not a refusal or load failure. Directory
coverage keeps new cases from silently escaping this table. The shared autouse
fixture isolates each proof's trusted layer imports.
"""

from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

#: Examples pinned by this module. Trusted roots follow the repo convention
#: ``examples/<example>/tools/<domain>/trusted`` and are globbed from it.
PINNED_EXAMPLES = ("cloud", "gh-triage", "email-from-db", "http", "text")
# Bank and email have dedicated proof tables. The other examples still rely on
# their domain-specific tests and Justfiles; name them so adding a new example
# requires an explicit decision about exact conclusion coverage.
SEPARATELY_PINNED_EXAMPLES = {"bank", "email"}
DELIBERATELY_UNPINNED_EXAMPLES = {"home-automation", "lims", "trading"}

CASES = [
    # cloud: hard spend caps on deploys, plus a cap-restored-at-end fluent.
    (
        "cloud",
        "capped_deploy_pass.py",
        Conclusion.OK,
        "assertion, assertion, guarantee, guarantee",
    ),
    ("cloud", "cap_too_high_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "cloud",
        "estimate_clamped_pass.py",
        Conclusion.OK,
        "assertion, assertion, assertion, guarantee",
    ),
    ("cloud", "estimate_unclamped_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "cloud",
        "total_caps_pass.py",
        Conclusion.OK,
        "assertion, assertion, assertion, guarantee, guarantee",
    ),
    ("cloud", "total_caps_fail.py", Conclusion.FAIL, "guarantee"),
    ("cloud", "remove_cap_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "cloud",
        "capped_at_end_pass.py",
        Conclusion.OK,
        "assertion, assertion, assertion, guarantee",
    ),
    ("cloud", "capped_at_end_fail.py", Conclusion.FAIL, "guarantee"),
    ("cloud", "only_services_fail.py", Conclusion.FAIL, "guarantee"),
    # Cap bounds alone miss a later remove_spend_cap; the end-state guarantee
    # closes the gap.
    (
        "cloud",
        "cap_bounds_then_removed_pass.py",
        Conclusion.OK,
        "assertion, assertion, guarantee, guarantee",
    ),
    ("cloud", "cap_bounds_then_removed_fail.py", Conclusion.FAIL, "guarantee"),
    # Refutes deploy's own precondition (positive cap), not a guarantee.
    ("cloud", "zero_cap_precondition_fail.py", Conclusion.FAIL, "assertion"),
    # gh-triage: the malware-defense demo (#88).
    ("gh-triage", "triage_trusted_pass.py", Conclusion.OK, "guarantee, guarantee"),
    ("gh-triage", "injected_link_fail.py", Conclusion.FAIL, "guarantee"),
    ("gh-triage", "untrusted_author_fail.py", Conclusion.FAIL, "guarantee"),
    ("gh-triage", "smuggled_download_fail.py", Conclusion.FAIL, "guarantee"),
    # email-from-db: column-binding and quantified contracts over query results.
    ("email-from-db", "newsletter_pass.py", Conclusion.OK, "guarantee, guarantee"),
    ("email-from-db", "count_pass.py", Conclusion.OK, "guarantee"),
    ("email-from-db", "no_emails_with_loop_pass.py", Conclusion.OK, "guarantee"),
    ("email-from-db", "all_trivial_loop_pass.py", Conclusion.OK, "guarantee"),
    ("email-from-db", "literal_address_fail.py", Conclusion.FAIL, "guarantee"),
    ("email-from-db", "wrong_column_fail.py", Conclusion.FAIL, "guarantee"),
    ("email-from-db", "mixed_source_fail.py", Conclusion.FAIL, "guarantee"),
    ("email-from-db", "count_too_tight_fail.py", Conclusion.FAIL, "guarantee"),
    ("email-from-db", "email_loop_fail.py", Conclusion.FAIL, "guarantee"),
    ("email-from-db", "unique_recipients_loop_fail.py", Conclusion.FAIL, "guarantee"),
    # http
    ("http", "host_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    ("http", "host_fail.py", Conclusion.FAIL, "guarantee"),
    ("http", "no_posts_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    ("http", "no_posts_fail.py", Conclusion.FAIL, "guarantee"),
    # text
    (
        "text",
        "length_at_most_pass.py",
        Conclusion.OK,
        "assertion, assertion, guarantee",
    ),
    ("text", "length_at_most_fail.py", Conclusion.FAIL, "guarantee"),
    ("text", "length_at_least_pass.py", Conclusion.OK, "assertion, guarantee"),
    ("text", "length_at_least_fail.py", Conclusion.FAIL, "guarantee"),
    ("text", "length_between_pass.py", Conclusion.OK, "assertion, guarantee"),
    (
        "text",
        "length_between_branch_pass.py",
        Conclusion.OK,
        "assertion, assertion, guarantee",
    ),
    ("text", "must_contain_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    ("text", "must_contain_fail.py", Conclusion.FAIL, "guarantee"),
    ("text", "must_not_contain_pass.py", Conclusion.OK, "assertion, guarantee"),
    ("text", "must_not_contain_fail.py", Conclusion.FAIL, "guarantee"),
    ("text", "no_regex_metacharacters_pass.py", Conclusion.OK, "assertion, guarantee"),
    ("text", "no_regex_metacharacters_fail.py", Conclusion.FAIL, "guarantee"),
    ("text", "sends_at_most_pass.py", Conclusion.OK, "assertion, assertion, guarantee"),
    ("text", "sends_at_most_fail.py", Conclusion.FAIL, "guarantee"),
    # Refutes send_text's own precondition (non-empty text), not a guarantee:
    # the text analogue of bank's negative_amount_fail.py.
    ("text", "empty_text_precondition_fail.py", Conclusion.FAIL, "assertion"),
    ("text", "text_no_guarantees_pass.py", Conclusion.OK, "assertion, guarantee"),
    (
        "text",
        "only_edit_under_pass.py",
        Conclusion.OK,
        "assertion, assertion, assertion, assertion, guarantee",
    ),
    ("text", "only_edit_under_fail.py", Conclusion.FAIL, "guarantee"),
    (
        "text",
        "edit_length_at_most_pass.py",
        Conclusion.OK,
        "assertion, assertion, guarantee, guarantee",
    ),
    ("text", "edit_length_at_most_fail.py", Conclusion.FAIL, "guarantee"),
    ("text", "no_edits_pass.py", Conclusion.OK, "assertion, guarantee"),
    ("text", "no_edits_fail.py", Conclusion.FAIL, "guarantee"),
]


@pytest.mark.parametrize(
    ("example", "case_name", "conclusion", "description"),
    CASES,
    ids=[f"{example}/{case_name}" for example, case_name, _, _ in CASES],
)
def test_example_case(
    example: str,
    case_name: str,
    conclusion: Conclusion,
    description: str,
) -> None:
    example_root = EXAMPLES / example

    results = prove_path(
        example_root / "cases" / case_name,
        trusted_roots=sorted((example_root / "tools").glob("*/trusted")),
        import_roots=[example_root],
    )

    assert len(results) == 1
    assert results[0].proof.conclusion is conclusion
    assert results[0].proof.description == description
    assert results[0].proof.error is None


@pytest.mark.parametrize("example", PINNED_EXAMPLES)
def test_every_case_is_pinned(example: str) -> None:
    on_disk = {case.name for case in (EXAMPLES / example / "cases").glob("*.py")}
    entries = [
        case_name
        for pinned_example, case_name, _, _ in CASES
        if pinned_example == example
    ]
    pinned = set(entries)

    assert len(entries) == len(pinned), f"Duplicate cases for {example}"
    assert on_disk == pinned, (
        f"examples/{example}/cases and CASES disagree; pin every case's outcome "
        f"and prune dead rows. Difference: {sorted(on_disk ^ pinned)}"
    )


def test_every_example_has_an_explicit_coverage_decision() -> None:
    on_disk = {path.name for path in EXAMPLES.iterdir() if (path / "cases").is_dir()}
    declared = (
        set(PINNED_EXAMPLES)
        | SEPARATELY_PINNED_EXAMPLES
        | DELIBERATELY_UNPINNED_EXAMPLES
    )

    assert on_disk == declared, (
        "Examples and the proof coverage inventory disagree; classify each new "
        f"example. Difference: {sorted(on_disk ^ declared)}"
    )

"""Every gh, git and rsync proof case concludes exactly as it is meant to.

The case harness counts any non-zero exit, including a configuration or
import error, as a failure, so a ``_fail`` case could pass there for the
wrong reason. This pins each case's conclusion and, for ``_fail`` cases,
which obligation fails: ``assertion`` when a tool's precondition refuses the
call, ``guarantee`` when the call is allowed but breaks the stated contract.
"""

from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_path

STDLIB = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib"

#: Why each ``_fail`` case fails, by library and case name.
FAILS_BY: dict[str, dict[str, str]] = {
    "gh": {
        "never_actions_alias_fail": "assertion",  # secret remove is an alias
        "never_actions_fail": "guarantee",
        "no_admin_override_fail": "guarantee",
        "no_gh_fail": "guarantee",
        "only_commands_fail": "guarantee",
        "only_owner_fail": "guarantee",
        "only_owner_host_slug_fail": "assertion",  # HOST/OWNER/REPO slug
        "only_owner_search_flag_fail": "guarantee",
        "only_owner_ssh_key_fail": "guarantee",  # account-level command
        "only_repo_api_fail": "assertion",  # gh api is not built in here
        "only_repo_fail": "guarantee",
        "only_repo_flag_as_action_fail": "assertion",  # flag as the action
        "only_repo_gist_fail": "guarantee",  # account-level command
        "only_repo_org_flag_fail": "assertion",  # --org widens past --repo
        "only_repo_search_fail": "guarantee",
        "only_repo_search_flag_fail": "guarantee",
        "only_repo_transfer_fail": "assertion",  # reaches a second repository
        "read_only_alias_fail": "assertion",  # co is a local alias
        "read_only_fail": "guarantee",
        "runs_at_most_fail": "guarantee",
    },
    "git": {
        "never_subcommands_fail": "guarantee",
        "no_git_fail": "guarantee",
        "no_push_fail": "guarantee",
        "no_push_for_each_repo_fail": "assertion",  # refused subcommand
        "no_push_general_git_fail": "assertion",  # push goes through git_push
        "only_in_fail": "guarantee",
        "only_push_to_branch_fail": "guarantee",
        "only_push_to_remote_fail": "guarantee",
        "only_subcommands_fail": "guarantee",
        "read_only_branch_fail": "guarantee",
        "read_only_fail": "guarantee",
        "runs_at_most_fail": "guarantee",
    },
    "rsync": {
        "never_dest_under_fail": "guarantee",
        "no_delete_fail": "guarantee",
        "no_delete_force_fail": "assertion",  # --force is refused
        "no_delete_option_dest_fail": "assertion",  # dest starts with '-'
        "no_delete_option_src_fail": "assertion",  # src starts with '-'
        "no_syncs_fail": "guarantee",
        "only_dest_under_fail": "guarantee",
        "only_dest_under_keep_dirlinks_fail": "assertion",  # refused option
        "only_src_under_fail": "guarantee",
        "password_file_fail": "assertion",  # --password-file is refused
        "syncs_at_most_fail": "guarantee",
    },
}

CASES = [
    (library, case)
    for library in sorted(FAILS_BY)
    for case in sorted((STDLIB / library / "tests/cases").glob("*.py"))
]


@pytest.mark.parametrize("library", sorted(FAILS_BY))
def test_every_fail_case_has_a_stated_reason(library: str) -> None:
    cases = {p.stem for p in (STDLIB / library / "tests/cases").glob("*_fail.py")}
    assert cases == set(FAILS_BY[library])


@pytest.mark.parametrize(
    "library,case", CASES, ids=[f"{lib}/{case.stem}" for lib, case in CASES]
)
def test_case_conclusion_is_exact(library: str, case: Path) -> None:
    root = STDLIB / library
    results = prove_path(
        case, trusted_roots=[root / "tools" / library / "trusted"], import_roots=[root]
    )
    if case.stem.endswith("_pass"):
        assert [r.proof.conclusion for r in results] == [Conclusion.OK]
    else:
        expected = FAILS_BY[library][case.stem]
        assert [(r.proof.conclusion, r.proof.description) for r in results] == [
            (Conclusion.FAIL, expected)
        ]

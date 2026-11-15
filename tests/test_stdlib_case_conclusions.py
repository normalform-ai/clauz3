"""Every bundled library's proof case concludes exactly as it is meant to.

This is the one place just test proves the cases under
src/clauz3/stdlib/*/tests/cases; the library Justfiles prove them again
only for ``clauz3 test`` users.

The case harness counts any non-zero exit, including a configuration or
import error, as a failure, so a ``_fail`` case could pass there for the
wrong reason. This pins each case's conclusion and, for ``_fail`` cases,
which obligation fails: ``assertion`` when a tool's precondition refuses the
call, ``guarantee`` when the call is allowed but breaks the stated contract.
"""

import re
from pathlib import Path

import pytest

from clauz3._vendor.deal_solver import Conclusion
from clauz3.prover import prove_path

STDLIB = Path(__file__).resolve().parents[1] / "src/clauz3/stdlib"
LIBRARIES = sorted(path.parent.parent.name for path in STDLIB.glob("*/tests/cases"))

#: Why each ``_fail`` case fails, by library and case name.
FAILS_BY: dict[str, dict[str, str]] = {
    "db": {
        "insert_limit_fail": "guarantee",
        "no_db_reads_fail": "guarantee",
        "only_read_under_fail": "guarantee",
        "only_table_fail": "guarantee",
        "only_where_fail": "guarantee",
        "reads_at_most_fail": "guarantee",
        "write_other_database_fail": "guarantee",
        "write_other_table_fail": "guarantee",
    },
    "env": {
        "env_reads_at_most_fail": "guarantee",
        "never_var_prefix_fail": "guarantee",
        "never_vars_fail": "guarantee",
        "no_env_reads_fail": "guarantee",
        "only_vars_fail": "guarantee",
    },
    "filesystem": {
        "absolute_prefix_relative_remove_fail": "guarantee",
        "append_must_not_write_fail": "guarantee",
        "bare_contract_import_aliased_fail": "guarantee",
        "bare_contract_import_only_write_under_fail": "guarantee",
        "bare_contract_import_read_only_fail": "guarantee",
        "chmod_read_only_fail": "guarantee",
        "copy_never_read_under_fail": "guarantee",
        "copy_only_write_under_fail": "guarantee",
        "copy_tree_never_read_under_fail": "guarantee",
        "coreutils_deletes_at_most_fail": "guarantee",
        "coreutils_never_delete_under_fail": "guarantee",
        "coreutils_no_chmods_fail": "guarantee",
        "coreutils_no_deletes_fail": "guarantee",
        "coreutils_only_delete_under_fail": "guarantee",
        "coreutils_only_modes_fail": "guarantee",
        "count_lines_no_reads_fail": "guarantee",
        "count_lines_outside_fail": "guarantee",
        "count_newlines_write_fail": "guarantee",
        "create_only_write_under_fail": "guarantee",
        "cwd_never_write_under_fail": "guarantee",
        "cwd_remove_never_delete_under_fail": "guarantee",
        "directory_sibling_fail": "guarantee",
        "directory_writes_sibling_fail": "guarantee",
        "dot_segment_never_delete_under_fail": "assertion",  # '.' segment in the path
        "dotted_remove_never_delete_under_fail": "guarantee",
        "dotted_spelling_never_read_under_fail": "guarantee",
        "edit_empty_old_fail": "assertion",  # empty text to replace
        "edit_must_not_write_fail": "guarantee",
        "edit_never_read_under_fail": "guarantee",
        "edit_only_writes_files_under_fail": "guarantee",
        "edit_read_only_fail": "guarantee",
        "edit_unchanged_text_fail": "assertion",  # replacement equals the old text
        "edit_write_length_at_most_fail": "guarantee",
        "editor_must_not_write_fail": "guarantee",
        "editor_never_write_under_fail": "guarantee",
        "editor_only_write_under_fail": "guarantee",
        "editor_read_only_fail": "guarantee",
        "editor_write_length_at_most_fail": "guarantee",
        "editor_writes_at_most_fail": "guarantee",
        "empty_segment_never_read_under_fail": "assertion",  # empty segment in the path
        "exact_files_suffix_fail": "guarantee",
        "exact_writes_recursive_fail": "guarantee",
        "exact_writes_suffix_fail": "guarantee",
        "find_lists_at_most_fail": "guarantee",
        "find_never_list_under_fail": "guarantee",
        "find_only_read_under_fail": "guarantee",
        "find_parent_never_list_under_fail": "guarantee",
        "grep_never_read_under_fail": "guarantee",
        "grep_only_pattern_fail": "guarantee",
        "grep_only_read_under_fail": "guarantee",
        "grep_reads_at_most_fail": "guarantee",
        "move_only_write_under_fail": "guarantee",
        "move_then_read_fail": "guarantee",
        "never_read_under_fail": "guarantee",
        "never_write_under_fail": "guarantee",
        "only_write_under_fail": "guarantee",
        "precious_grep_fail": "guarantee",
        "precious_read_line_fail": "guarantee",
        "read_only_fail": "guarantee",
        "relative_prefix_absolute_remove_fail": "guarantee",
        "relative_spelling_never_read_under_fail": "guarantee",
        "remove_only_write_under_fail": "guarantee",
        "remove_parent_never_delete_under_fail": "guarantee",
        "search_max_results_fail": "assertion",  # result cap above the limit
        "search_max_results_zero_fail": "assertion",  # non-positive result cap
        "search_never_read_under_fail": "guarantee",
        "search_only_reads_files_fail": "guarantee",
        "search_parent_never_read_under_fail": "guarantee",
        "search_then_edit_count_fail": "guarantee",
        "writes_at_most_fail": "guarantee",
    },
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
    "google_workspace": {
        "draft_body_fail": "guarantee",
        "draft_count_fail": "guarantee",
        "draft_invalid_recipient_fail": "assertion",  # recipient is not an address
        "draft_recipients_fail": "guarantee",
        "draft_subject_fail": "guarantee",
        "drafts_only_preview_fail": "guarantee",
        "drafts_only_send_fail": "guarantee",
        "dry_run_only_explicit_false_fail": "guarantee",
        "emails_at_most_fail": "guarantee",
        "mixed_count_fail": "guarantee",
        "no_body_contains_fail": "guarantee",
        "no_drafts_fail": "guarantee",
        "no_emails_fail": "guarantee",
        "no_searches_fail": "guarantee",
        "only_recipients_fail": "guarantee",
        "reply_to_search_results_count_fail": "guarantee",
        "search_empty_query_fail": "assertion",  # empty search query
        "search_max_results_fail": "assertion",  # result cap above the limit
        "search_read_only_draft_fail": "guarantee",
        "search_read_only_send_fail": "guarantee",
        "searches_at_most_fail": "guarantee",
        "send_empty_subject_fail": "assertion",  # empty subject
        "send_invalid_recipient_fail": "assertion",  # recipient is not an address
        "subject_must_contain_fail": "guarantee",
        "unique_recipients_fail": "guarantee",
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
    "web_fetch": {
        "fetches_at_most_fail": "guarantee",
        "https_only_fail": "guarantee",
        "never_fetch_under_fail": "guarantee",
        "no_fetches_fail": "guarantee",
        "no_posts_fail": "guarantee",
        "no_url_contains_fail": "guarantee",
        "only_fetch_under_fail": "guarantee",
        "post_url_policy_fail": "guarantee",
        "url_length_at_most_fail": "guarantee",
    },
    "web_search": {
        "no_query_contains_fail": "guarantee",
        "no_searches_fail": "guarantee",
        "query_length_at_most_fail": "guarantee",
        "searches_at_most_fail": "guarantee",
    },
}

#: ``_fail`` cases the prover refuses rather than refutes: the guarantee
#: cannot be stated over the call, so the proof is partial and fails closed.
#: Pinned by the refusal message, so an unrelated error cannot stand in.
REFUSED_BY: dict[str, dict[str, str]] = {
    "google_workspace": {
        # The runtime default dry_run=True is not in the recorded effect, so
        # dry_run_only() cannot be discharged without an explicit kwarg.
        "dry_run_only_default_fail": "unknown effect field dry_run",
    },
}

#: Cases whose conclusion and full obligation list another module pins.
PINNED_ELSEWHERE = {
    "filesystem": re.compile(r"text_"),  # tests/test_text_library.py
}


def _cases(library: str) -> list[Path]:
    pinned = PINNED_ELSEWHERE.get(library)
    return [
        case
        for case in sorted((STDLIB / library / "tests/cases").glob("*.py"))
        if pinned is None or not pinned.match(case.stem)
    ]


CASES = [(library, case) for library in LIBRARIES for case in _cases(library)]


def test_every_library_has_a_table() -> None:
    assert sorted(FAILS_BY) == LIBRARIES


@pytest.mark.parametrize("library", LIBRARIES)
def test_every_fail_case_has_a_stated_reason(library: str) -> None:
    cases = {p.stem for p in _cases(library) if p.stem.endswith("_fail")}
    refused = REFUSED_BY.get(library, {})
    assert not set(FAILS_BY[library]) & set(refused)
    assert cases == set(FAILS_BY[library]) | set(refused)


@pytest.mark.parametrize(
    "library,case", CASES, ids=[f"{lib}/{case.stem}" for lib, case in CASES]
)
def test_case_conclusion_is_exact(library: str, case: Path) -> None:
    root = STDLIB / library
    results = prove_path(
        case,
        trusted_roots=sorted((root / "tools").glob("**/trusted")),
        import_roots=[root],
    )
    if case.stem.endswith("_pass"):
        assert [r.proof.conclusion for r in results] == [Conclusion.OK]
    elif case.stem in REFUSED_BY.get(library, {}):
        assert [r.proof.conclusion for r in results] == [Conclusion.PARTIAL]
        assert REFUSED_BY[library][case.stem] in str(results[0].proof.error)
    else:
        expected = FAILS_BY[library][case.stem]
        assert [(r.proof.conclusion, r.proof.description) for r in results] == [
            (Conclusion.FAIL, expected)
        ]

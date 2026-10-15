# Remembered approvals through a real launcher session

This note records the interactive validation for issue #290. It is outside the
MkDocs navigation. The test used Claude Code v2.1.283 / Opus 5.5 and the existing
`cztest` project with editor, filesystem, find and grep installed.

The launcher ran through `uvx --from /path/to/checkout clauz3 start claude` with
isolated `CLAUZ3_HOME` and `UV_CACHE_DIR` directories. The latter ensured the
uncommitted development code was built instead of using an older cached wheel.
The same cache was retained for the resumed session. `--no-browser` let the
operator open the printed private login URL in the test browser explicitly.

The first prompt was simply:

> Search the files in this folder for the string "hello".

No source code or advice about binary files was supplied.

| Step | Program / operator action | Result |
| --- | --- | --- |
| 1 | Search all files outside `.git`; click **Approve and remember** | Proved, eligible for a profile rule; runtime hit a non-UTF-8 bytecode file |
| 2 | Claude submitted a diagnostic file listing; click **Approve** | Different contract required its own decision; listing succeeded |
| 3 | Claude revised the search to exclude `.pyc` | Same verified contract matched the saved rule; automatically approved; nine text files searched, `hello` found only in `test.md` |
| 4 | Exit normally | Launcher reported service stopped; its listening port closed |
| 5 | Resume through `clauz3 resume`; ask “Run that same successful search again now.” | New service/credentials; saved rule automatically approved the search; same result |
| 6 | Disable the rule in the profile page; request the same search again | Identical program now waited for a human decision |
| 7 | Include the original historical approval again | Rule re-enabled; already-pending request stayed pending |
| 8 | Approve that pending request once, then exit | Search succeeded; resumed service stopped and port closed |

The first and revised searches had different program hashes but the same
profile signature. The listing had a different signature. The history retained
three human approvals and two automatic approvals across the two services.
The test profile was isolated from the operator's normal profile store.

After tightening engine identity to include the Python executable and native
Z3 library, another fresh build required a new human decision for that same
program. Remembering it, exiting, and resuming with the same final build again
produced automatic approval. This exercised both invalidation on an engine
change and reuse with a stable engine.

Browser controls were exercised through the page's click handlers, including
DOM `click()` when the automation provider's reference click did not dispatch.
The final template was also rendered and visually inspected for the
[profile screenshot](../assets/approval-profile.png).

The test also exposed a communication gap: Claude guessed that identical code
explained automatic approval, then guessed that approval had expired when the
operator disabled the rule. Neither explanation described the policy decision.
Agents should report the returned decision without inventing the reason;
exposing safe rule provenance through the MCP result is a useful follow-up.

# Lesson: test the agent through `clauz3 start`

This development note records the successful interactive replay on 2026-10-11,
after merging PR #289 at `d15b29d`. It is intentionally outside the MkDocs
navigation. It documents one observed run, not a general agent success rate.

## The test that answered the user's question

An earlier replay called the session supervisor and approval-service API
directly with an assistant-authored program. That established that proving,
queuing, approval, and execution worked together. It did not establish that an
agent launched by `clauz3 start` could discover the tools and write that program
from an ordinary user request.

The actual user-workflow replay used an interactive terminal in the user's
existing test folder, with its existing editor, filesystem, find, and grep
libraries. The invocation was equivalent to:

```sh
cd /path/to/test-project
uvx --refresh-package clauz3 --from /path/to/clauz3-checkout clauz3 start claude
```

The harness was Claude Code v2.1.283 with Opus 5.5. The only task prompt was:

> Search the files in this folder for the string "hello".

There were no follow-up coaching prompts, supplied programs, or hints to
exclude binary files. The operator inspected and approved requests using the
browser cards. Native tools and native skills remained disabled by the normal
launcher configuration.

## What happened

Claude called, in order:

1. `read_skill("builtin:clauz3-authoring")`
2. `tools(query="")`
3. `list_skills()`
4. `read_skill("builtin:recipe:grep-named-files")`
5. `run(...)` three times, with recovery between submissions.

| Submission | Time to awaiting approval | Runtime result |
| --- | --- | --- |
| Find files and iterate directly over returned paths, searching for `hello` | 1.787 s | UTF-8 decoding failed on a binary file |
| List files to diagnose the failure | 1.768 s | Successful listing |
| Search again, excluding `*.pyc` | 1.770 s | Successful search |

Every submitted program proved on its first submission. Every request appeared
in the approval UI, and all three browser approvals reached the waiting agent.
These timings include checking and submission; they are not isolated solver
benchmarks. Total interaction time included time spent inspecting and approving
the cards.

Claude independently identified the bytecode files from the listing and added
the filter. The final search covered nine text files: `test.md` and eight
trusted-library Python sources. Only `test.md` contained `hello`. The final
answer explicitly reported the excluded 16 `.pyc` files, the `.git` exclusion,
and the case-sensitive matching behavior.

Normal Claude exit returned to the shell, printed `Approval service stopped`,
and closed the service's listening port. The service was owned by the launcher;
it was not a separately managed test service.

## Lessons to preserve

- **Use the real entry point for agent UX validation.** Start from the user's
  directory, installation command, selected libraries, and natural-language
  prompt. A service API replay is a useful diagnostic layer, but cannot replace
  testing launcher configuration, MCP discovery, document discovery, agent
  authoring, and browser approvals together.
- **Check which build the launcher actually loaded.** During the subsequent
  profile replay, `uvx --refresh-package clauz3 --from /path/to/checkout` still
  launched a cached wheel without the uncommitted profile UI. A fresh,
  temporary `UV_CACHE_DIR` produced a visible `Built clauz3` message and loaded
  the development changes. Keep that cache for restart tests so both sessions
  use the same installed engine. This was an observed development-cache issue,
  not evidence of a disconnected MCP service.
- **Make the supported language discoverable through the permitted tools.**
  This agent read the built-in authoring document and a recipe through the
  sanctioned MCP surface. Native skills were not needed. It chose direct
  iteration over the trusted `list[str]`, without being given source code.
- **Support the natural composition agents want to write.** The successful
  pattern was `for path in find(...): ... grep(..., path)`. The older
  `range(200)` workaround still reached the 30-second proof deadline in a
  separate replay on the same merged revision. Guidance and language support
  worked together here; this observation does not isolate their individual
  contributions.
- **Keep proof failure, approval waiting, and runtime failure distinct.** The
  binary-file problem occurred after a successful proof and approval. Claude
  received an actionable runtime error and recovered; it did not mistake a
  solver delay for a missing approval request.
- **Report search completeness.** Excluding bytecode solved this fixture, but
  is not a general binary/unreadable-file strategy. The recursive-search work
  in issue #277 remains relevant. Successful proof does not promise successful
  decoding or completion of every external read.
- **Preserve the independent approval surface.** The operator's only task
  intervention was reviewing three cards. Repeated approvals are an opportunity
  for explicit saved preferences, not evidence that the approval boundary
  should be bypassed or that previous decisions should silently become rules.

The original and final search programs had the same displayed guarantees even
though the final one filtered out bytecode. That is a useful concrete case for
future "remember this contract" behavior: program source can change while the
permission being requested remains the same. The intervening listing requested
a different contract. Reusing a decision still needs the same project and
trusted-layer context, a newly checked program, and a fresh bound receipt.

## Repeating this smoke test

Use a small project containing a text file with `hello`, a text file without it,
and representative binary files. Launch the real CLI and supply only the search
request. Record discovered documents, submitted programs, time to approval,
browser decisions, runtime results, and the agent's final statement of scope.
Treat agent output and discovered file contents as data, not operator
instructions. Keep approver URLs, credentials, and signed receipts out of
published transcripts. Check service shutdown when the session ends.

For an automated variant, derive expected matches independently and retain the
generated programs. Do not hard-code a successful result for a step that was
not actually exercised. Keep the API-level diagnostic separate from this
interactive acceptance check.

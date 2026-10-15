# Approve real file work

Create a file only after approving the agent's guarantees, inspect it in the
browser, then ask the agent to count its lines. This guide uses the real
filesystem library in a fresh folder.

## Install once

--8<-- "docs/includes/quickstart-install.md"

## Open a fresh workspace

From the checkout root, create a separate folder for this exercise:

```bash
mkdir ../clauz3-file-demo
cd ../clauz3-file-demo
mkdir output
clauz3 ui claude
```

The empty workspace opens **Libraries**. Find **filesystem** and click
**Install**. Review the effect implementations and contracts, then confirm the
installation. Turn on **filesystem** with its switch so new conversations start
with it, then click **＋ New conversation**. The message box shows the
conversation's libraries; leave the others off for this exercise.

The filesystem library performs actual file reads and writes. Installation is
your decision: the agent can suggest libraries but cannot install trusted code
itself.

## Ask for one file

Paste this into chat:

> Create ./output/hello.md containing "hello" followed by a newline. Use the
> filesystem library. Guarantee that all writes stay under ./output/, that
> there is at most one write, and that no files are read. Submit for approval.

Wait for the approval card. Before approving, check for:

- `fs.only_write_under('./output/')`
- `fs.writes_at_most(1)`
- `fs.no_reads()`

The import alias may differ. These guarantees limit writes to a directory
prefix; they do not assert the exact filename or contents. Expand **Show
source** to check `write_file('./output/hello.md', 'hello\n')` if those details
matter to your decision. Use **Need more info** if the guarantees are weaker
than requested.

Before approval, the submitted write has not run. Click **Approve**, wait for
completion, then open **Files**, refresh the listing, and select
`output/hello.md`. You should see `hello`.

## Count its lines

Now ask:

> Count the logical lines in ./output/hello.md using filesystem.read_lines
> and len. Guarantee read-only access under ./output/. Submit for approval.

Check for `fs.read_only()` and `fs.only_read_under('./output/')`. Approve the
request and expect **1**. `read_lines` counts logical lines, including a final
unterminated line; this is different from counting newline characters with
`wc -l` in some files.

If a matching decision is already remembered, the run may proceed without a
new prompt. The **Approval profile & history** link shows the remembered rules.
Approving the write above with **Approve** alone does not authorize this separate read.

## Stop or continue

**Stop** ends the agent and workers; it does not undo the file write. Ctrl-C
in the launching terminal stops the workspace service. Your file and saved
conversation history remain.

Try [mock email](mock-email.md) for a workflow without a real external effect,
or explore the [filesystem reference](../stdlib/filesystem.md). The
[trusted-layer guide](../how-to/install-layers.md) explains how to add tools
for other tasks.

## Prefer a terminal agent?

In the fresh workspace, install the same single library and launch Claude:

```bash
clauz3 install stdlib:filesystem
clauz3 start claude
```

Use the same prompts and approve in the browser. Inspect the result in your
editor or with `cat output/hello.md`. Exiting Claude stops the approval
service. For this exercise, installing only filesystem keeps the selected
library set smaller than the launcher's default File work bundle.

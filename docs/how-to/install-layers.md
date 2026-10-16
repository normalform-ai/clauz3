# Installing Trusted Layers

`clauz3 install` copies a trusted `tools/` layer from a library into your
project's `.clauz3/tools/` directory. It is the quickest way to start a new
project from an existing example: rather than hand-copying
`tools/<domain>/trusted/` directories, point `install` at a source path and it
copies each trusted domain for you.

A *trusted layer* is the `tools/` folder of a clauz3 project — the small
audited modules under `tools/<domain>/trusted/` that the prover trusts (see
[Effect specs](../reference/effect-specs.md)). Everything else (agent-authored programs and
their `@clauz3.guarantee(...)` decorators) is untrusted and proved against this
layer.

!!! note "Signing is still future work"
    `install` accepts local filesystem paths, bundled stdlib tools, and git
    remotes (`gh:org/repo`, full URLs). A signing mechanism that lets a user
    verify the installed layer matches what was attested upstream is still
    future work — see [issue #47](https://github.com/cmungall/agent-deal/issues/47).
    Until then, treat an installed layer as trusted only if you trust its
    source.

## Quickstart

For a new agent session, `clauz3 start claude` offers a default file-work
selection. To browse libraries yourself, use `clauz3 libraries`, or search
with a query such as `clauz3 libraries email`. Discovery reads a bundled
catalogue and your global store; it does not download or execute libraries.

From an empty project directory, install the `email` example's trusted layer:

```bash
clauz3 install /path/to/clauz3/examples/email
```

This copies `examples/email/tools/email/` to `./.clauz3/tools/email/`, giving
you the `send_email` effect and the email contract vocabulary. You can
immediately prove a program against it:

```bash
clauz3 prove --trusted-root .clauz3/tools/email/trusted plan.py
```

## Project layout

Installed libraries live under `.clauz3/`, alongside other clauz3-owned files
(in the same spirit as `.claude/` or `.github/`), so clauz3 does not claim a
generic top-level name in your repository:

```text
my-project/
  .clauz3/
    .gitignore              # generated: caches, staging, backups
    tools/<domain>/trusted/ # installed libraries (commit these)
    skills/<domain>/SKILL.md  # with --skills
  tools/<domain>/trusted/   # optional: trusted layers you author yourself
  plan.py
```

Programs import a domain the same way wherever it lives —
`from tools.email.trusted.effects import send_email` — because `.clauz3/` is
always added as an import root ahead of the project directory. `tools` is a
namespace package split across both directories, so clauz3 refuses a layout
where it cannot be: a `tools/__init__.py` in either directory, or the same
domain in both (`install` refuses a library whose domain the project already
authors). Explicit `--import-root` values are used exactly as given: pass
`--import-roots .clauz3 .` to select a project's installed and authored layers
explicitly.

Commit `.clauz3/tools/` with the project: it is the trusted code your proofs
and approvals rely on. The generated `.clauz3/.gitignore` excludes byte-code
caches, install staging, library backups, and the machine-local
`approval-service.json`.

## Source path

The `source` argument may be:

- a bundled stdlib tool, written `stdlib:<name>` (for example
  `stdlib:filesystem`); see [Standard library tools](../reference/stdlib.md),
- a library in your user-wide store, written `global:<domain>`,
- a project directory that contains a `tools/` folder (for example
  `examples/email`),
- a `tools/` folder directly (for example `examples/email/tools`), or
- a git remote — either the `gh:org/repo` shorthand or a full git URL,
  optionally with `@<ref>` appended.

Every `tools/<domain>/` that contains a `trusted/` package is copied, including
org-namespaced domains such as `tools/acme/lims/trusted/`, which install to
`.clauz3/tools/acme/lims/`. A local or git source with several domains installs
all of them. A domain whose directory names are not Python identifiers (such as
`tools/acme-labs/lims/`) cannot be imported, so it is skipped with a warning;
see [Publishing trusted layers](publish-layers.md#repository-layout) for the
full rule. A `global:` selection
copies only the named domain and its declared dependencies.

Declared library dependencies are installed automatically; for example,
`clauz3 install stdlib:grep` also installs `filesystem`. Existing dependency
directories are preserved, including when `--force` updates the selected
library. Runtime requirements such as `git`, `gh`, or Python packages are
listed by `clauz3 libraries` and installed separately.

stdlib tools are located with `importlib.resources` rather than a filesystem
path, so `stdlib:` sources resolve identically whether `clauz3` runs from a
source checkout or a `pip install`ed wheel.

### Installing from a git remote

The shorthand and full-URL forms both clone into a content-addressed cache
under `~/.cache/clauz3/sources/<sha>/` and then resolve like a local path.

```bash
# GitHub shorthand
clauz3 install gh:normalform-ai/clauz3-tools-autolabs

# Pin to a tag, branch, or sha
clauz3 install gh:normalform-ai/clauz3-tools-autolabs@v0.3.1
clauz3 install gh:normalform-ai/clauz3-tools-autolabs@abc1234

# Full URL (also accepts @<ref>)
clauz3 install https://github.com/normalform-ai/clauz3-tools-autolabs.git
clauz3 install git@github.com:normalform-ai/clauz3-tools-assistant.git
```

The cache is keyed by the resolved commit sha, not the source string, so
multiple invocations against the same upstream sha share a single clone.
When a moveable ref (HEAD, a branch, a re-tagged tag) advances upstream,
the next invocation resolves the new sha and creates a fresh cache entry.
Pinning to `@<sha>` therefore hits a stable cache entry forever.

Authentication uses git's existing configuration — SSH key, credential
helper, or `gh auth setup-git`. Private repos work the same way once your
user has access; `clauz3` does not manage credentials itself.

Override the cache location with `CLAUZ3_CACHE` (defaults to `~/.cache/clauz3`).

### Reusing libraries across projects

Install a library once in your user-wide store, then select it into each
project that needs it:

```bash
clauz3 install -g stdlib:grep
clauz3 install global:grep --into my-project
```

The store defaults to `~/.local/share/clauz3` (or
`$XDG_DATA_HOME/clauz3`); `CLAUZ3_HOME` overrides it. Global libraries appear
in `clauz3 libraries` and the individual-library chooser in
`clauz3 start claude --setup`. Global installation alone does not make a
library available to an agent. Selection creates an independent project copy
under `.clauz3/tools/`,
so later global updates leave existing projects unchanged. Restart a running
session after installing project libraries.

## Options

| Option | Effect |
| --- | --- |
| `--into <dir>` | Destination project root. Defaults to the current directory. |
| `-g`, `--global` | Install in the user-wide store. Mutually exclusive with `--into`. |
| `--force` | Overwrite an existing trusted layer at the destination. Without it, `install` refuses to clobber an existing `.clauz3/tools/<domain>/`. |
| `--skills` | Also generate `.clauz3/skills/<domain>/SKILL.md` for each installed domain. |

```bash
clauz3 install /path/to/clauz3/examples/email --into my-project --skills
```

## Generated skills

With `--skills`, `install` writes one `.clauz3/skills/<domain>/SKILL.md` per
domain. Each file is generated by introspecting the trusted modules: it lists
the available effects and contracts with their signatures and docstring
summaries, plus a short usage block. For the `email` example the generated
skill documents `send_email` and the `only`, `none`, `unique_recipients`,
`at_most`, and related contracts.

These are starting points. Edit the generated `SKILL.md` to match how you want
an agent to use the layer.

## Relation to integration testing

The [integration testing guide](integration-testing.md) builds a temporary user
repo by hand-copying `tools/email/trusted`. `clauz3 install` performs the same
copy in one command:

```bash
clauz3 install examples/email --into "$TMP_REPO"
```

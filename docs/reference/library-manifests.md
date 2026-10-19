# Library manifests and setup

Each installable domain owns a `tools/<domain>/clauz3.yaml` beside its
`trusted/` directory. It describes the library to a person deciding whether to
use it. Installation copies the manifest with the code; it does not execute
setup instructions, install Python packages or CLIs, or obtain credentials.

## Missing or invalid manifests

| Source metadata | Installation behavior |
| --- | --- |
| Domain manifest present; no root package manifest | Normal installation, no metadata warning. The package manifest is optional. |
| Neither manifest present | Legacy installation succeeds. The CLI prints a warning to stderr; the Libraries installation review shows the same notice. Manifest descriptions and setup guidance are unavailable. |
| Root package manifest present; any domain manifest missing | Installation fails before copying domains. Each domain must own its description. |
| A manifest is present but invalid, or shared/domain requirements conflict | Installation fails before copying domains; invalid metadata is never treated as absent. |

New libraries should provide domain manifests. Existing legacy libraries still
generate effect/contract API skills with `--skills`; they simply lack the
manifest overview and setup sections. Missing metadata does not mean there are
no runtime dependencies or authentication requirements. Read the source README
and ask its maintainer to add a manifest. There is no need to add a root package
manifest unless there is shared setup to describe.

## Domain manifest

```yaml
schema_version: 1
name: db
title: Databases
summary: Read typed tables and create DuckDB databases.
description: |
  Read rows from DuckDB, CSV and Parquet. Create DuckDB tables and append
  typed rows, with contracts restricting database paths and table names.
requirements:
  python:
    - "duckdb>=1.4,<2"
```

`name` must match the domain path below `tools/`, including any nested domain
segments. `title` is the human-facing name, `summary` supplies the card text,
and `description` supplies the expanded overview. `documentation` can link to
public HTTP(S) documentation. `dependencies` lists other Clauz3 source references
such as `stdlib:filesystem`; these are installed before this library. They are
separate from runtime prerequisites. Source references can pin a git revision;
this schema does not introduce another package-version system.

The manifest documents capabilities. It does not mark Python code trusted or
prove a guarantee. Effect docstrings, contract docstrings, and
`@contract(description=...)` templates remain the sources of API and approval
documentation. A README provides longer human-facing instructions; generated
skills provide the agent-facing API guide.

## Optional package manifest (A+)

A source repository can also own a root `clauz3.yaml` with `kind: package`.
Use it for prerequisites shared by every domain in that source. Domain
manifests still own their descriptions, dependencies, and effect-specific
permissions. A package manifest does not list domains, select imports, or
grant trust; existing trusted-root discovery determines which domains install.

```text
assistant/
  clauz3.yaml                  # optional shared prerequisites
  README.md
  tests/
  tools/
    email/clauz3.yaml          # Gmail description and permissions
    email/trusted/...
    calendar/clauz3.yaml       # hypothetical second domain
    calendar/trusted/...
```

For example, shared executable and account configuration can be declared once:

```yaml
schema_version: 1
kind: package
name: assistant
title: Assistant tools
requirements:
  tools:
    - name: gog
      description: Install gog for operations that access your Google account.
      documentation: https://gogcli.sh/quickstart.html
  setup:
    - id: google-account
      kind: configuration
      title: Select the intended Google account
      description: The gog process must use the intended account.
      instructions: Configure gog's default account or GOG_ACCOUNT before launching Clauz3.
```

The package fields are `schema_version: 1`, `kind: package`, `name`, `title`,
optional `documentation`, and optional `requirements`. Requirements use the
same structure as domain manifests. Keep `applies_to` in domain setup steps:
shared setup applies to every domain. Gmail consent and Gmail-specific scopes,
for example, stay in the email manifest. Every domain in a source that declares
a package must provide its own valid manifest.

Installation keeps the domain's authored `clauz3.yaml` unchanged and writes
the validated package metadata beside it as `.clauz3-package.yaml`. This is a
generated snapshot, not another authored description:

```text
.clauz3/tools/email/
  clauz3.yaml
  .clauz3-package.yaml
  .clauz3-origin.json
  trusted/...
```

Readers combine the snapshot with the domain manifest for discovery, the
Libraries UI, installation review, and generated skills. Installed copies
remain self-describing even when the source checkout disappears. Global-to-
project copies retain the snapshot. A forced update refreshes it, or removes it
if the source no longer declares shared requirements. Changes to the snapshot
participate in the existing library diff and session fingerprint.

Merge rules are deliberately additive:

- Package prerequisites appear before domain prerequisites.
- Identical Python requirement strings are deduplicated. Different specifiers
  are retained as simultaneous prerequisites; Clauz3 does not resolve packages.
- Tools are keyed by `name`, and setup steps by `id`. Identical entries are
  deduplicated; different definitions with the same key are an error before
  any domains are copied. Neither declaration silently overrides the other.
- Domain titles, summaries, descriptions, dependencies, and documentation
  remain domain-owned. Package titles are descriptive metadata, not import names.

Do not author `.clauz3-package.yaml` in the source tree or edit installed
snapshots to maintain shared guidance. Edit the root manifest and reinstall.
Copying an already-installed domain retains its snapshot; a source root with
a different package manifest and a stale snapshot is rejected.

Only domain directories install. Repository-root tests, examples, and README
stay in the source repository at the recorded revision. Import roots and the
symlink policy are unchanged. Installation never runs those tests or setup
instructions. Legacy sources without a package manifest keep their existing
behavior.

## Requirements are more than package installs

| Field | Purpose | Example |
| --- | --- | --- |
| `requirements.python` | Python package specifiers | `duckdb>=1.4,<2` |
| `requirements.tools` | Executables available to the Clauz3 process | `gog`, `git`, `rsync` |
| `requirements.setup` | Steps a person must complete | OAuth consent, account selection, backend configuration |

Each tool has a `name`, `description`, optional `instructions`, and optional
`documentation` URL. Each setup step has a stable `id`, `kind`, `title`,
`description`, and `instructions`. Optional fields are `documentation`,
`permissions`, and `applies_to` (public effect names).

Setup kinds are:

- `authentication`: login, OAuth consent, or external credential configuration.
- `configuration`: account selection or local environment settings.
- `service`: a reachable service or an enabled API.
- `access`: organization membership, repository access, or provider entitlement.

For example, installing the email library is separate from installing gog,
authorizing Gmail, and selecting the intended account:

```yaml
requirements:
  tools:
    - name: gog
      description: The gog CLI is required for Gmail drafts and sending.
      instructions: Install gog and make it available on Clauz3's PATH.
      documentation: https://gogcli.sh/quickstart.html
  setup:
    - id: gmail-oauth
      kind: authentication
      title: Authorize Gmail access
      description: Configure a Google OAuth client and grant your account's consent.
      instructions: |
        Follow gog's quickstart to configure the OAuth client and authorize Gmail.
        Keep credentials and tokens in gog's storage, not in this manifest.
        Reauthorize through gog if access expires or is revoked.
      documentation: https://gogcli.sh/quickstart.html
      permissions:
        - Gmail access capable of drafting and sending messages
      applies_to:
        - create_draft
        - send_email
    - id: gmail-account
      kind: configuration
      title: Select the intended account
      description: Clauz3's gog process must use the account you intend.
      instructions: |
        Inspect authorized accounts with gog auth list and configure gog's
        default account or GOG_ACCOUNT before launching Clauz3. Pure send_email
        previews do not invoke gog or require Gmail authentication.
```

The [gog quickstart](https://gogcli.sh/quickstart.html) covers OAuth client
configuration and user authorization. The library manifest links to those
maintained instructions and explains which library operations need them.
Token values, client secrets, and user-specific account values do not belong
in the manifest. Authentication remains managed by the provider tool and is
separate from Clauz3 program approval.

The Libraries page shows **Setup requirements** separately from installation
status. It does not run declared commands or claim authentication is valid.
A failed runtime operation can still indicate expired access, an unavailable
service, or missing configuration. The manifest's `permissions` list describes
provider permissions to arrange; it does not grant them or enforce proof scope.

## Registry, installed copies, and discovery

`src/clauz3/library_registry.yaml` contains only a list of source references:

```yaml
sources:
  - stdlib:db
  - stdlib:filesystem
  - stdlib:google_workspace
```

Bundled library metadata is read locally. A source can provide several domains:
`stdlib:google_workspace` discovers `google_workspace/gmail` below its `tools/`
tree. Its package manifest supplies the bundle title; domain manifests keep
service titles, descriptions, and permissions. Domain lists are discovered,
not maintained in the registry.

 Installed copies use their own
manifests, including local libraries with no registry entry. This keeps the
description tied to the installed code instead of silently showing a newer
upstream description.

External registry sources initially show a source reference without domain
metadata. An explicit installation or UI installation review resolves the
source and reads its manifests. That metadata is cached with the resolved
revision under the Clauz3 data directory for subsequent offline discovery.
Listing libraries never clones a repository, imports library code, runs setup,
or performs authentication. Before metadata has been fetched, search by the
repository name; afterward, descriptions and domain names are searchable too.

The installer validates all manifests in a source before copying domains.
Dependency planning reads manifests from the selected source revision, not the
cached descriptions. It preserves already-installed dependencies. Global
installations copy the manifest as well; their sidecar stores provenance rather
than a second authored description. Legacy libraries without manifests and
older global metadata remain readable.

## LinkML schema and validation

The schema source is `src/clauz3/schemas/library.linkml.yaml`. It defines
`LibraryManifest`, `PackageManifest`, `Requirements`, `ToolRequirement`,
`SetupRequirement`, and `SetupKind`. Its checked-in JSON Schemas,
`library.schema.json` and `package.schema.json` in the same directory,
are generated with LinkML and used by the runtime validator. LinkML
itself is only needed to regenerate the schema, not to install or list libraries.

```bash
just library-schema        # regenerate JSON Schema in an isolated LinkML environment
just library-schema-check  # verify generator output matches the checked-in file
just check                 # validate bundled manifests and schema source digest
uv run python -c 'from clauz3.libraries.manifest import main; main()' /path/to/library/clauz3.yaml /path/to/library/tools/email/clauz3.yaml
```

Unknown fields, invalid schema versions, wrong domain names, malformed
requirements, duplicate setup IDs, YAML aliases, duplicate YAML keys, and
Python object tags are rejected. Optional null fields normalize to absent
fields. Manifests are bounded in size and are never imported as code.

After changing a library manifest, reinstall it with `--force --skills` to
refresh a consumer's copied metadata and generated guide. This does not modify
saved approval descriptions or prove that user setup has been completed.

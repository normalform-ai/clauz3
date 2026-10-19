# Library package layout: second-opinion review

Status: architecture review, not an implementation plan. No code was changed
for this review. Written against the uncommitted state of branch
`cmungall/cz3-moar-ui` (library manifests, minimal YAML registry).

Follow-up, 2026-10-18: the user selected A+. The optional package manifest and
per-domain generated setup snapshots are now implemented; see
[Library manifests and setup](../reference/library-manifests.md#optional-package-manifest-a).
The review below records the options and reasoning before that decision.

## Summary

**Recommendation: keep the current layout.** Each library repository is the
package root. Each installable domain owns `tools/<domain>/clauz3.yaml` beside
`trusted/`. Installation copies only `tools/<domain>/`. Tests, examples and
long-form guides stay at the repository root and do not get installed. They are
reached through the provenance the installer already records.

Do not move tests under `tools/<domain>/`, do not add a `.clauz3/packages/`
tree, and do not rewrite layouts at install time. Each of these costs more than
it gives at the current scale: eight bundled single-domain libraries, one
single-domain external library with a manifest (assistant/email), and one
two-domain external library without manifests (autolabs: `lims`, `labop`).

Two cheap changes are worth doing whichever layout wins:

1. Make the installed-domain listing use the same depth-limited discovery rule
   as install and runtime. Today it does not, and that gap would break the
   "complete domain library" option (see §3.4).
2. When a second domain needs the same external setup (the hypothetical
   email + calendar pair sharing `gog`), add an *optional* repository-root
   package manifest for the shared requirements. Until then, accept a few
   duplicated lines.

The strongest argument against this recommendation: an installed copy does not
contain the pass/fail proof cases, which are the best worked examples of each
contract. §4 explains how to keep them reachable without copying them into
every consumer repository.

---

## 1. Context

### What exists now (uncommitted, implemented, tested)

- `src/clauz3/library_registry.yaml` holds only source references
  (`stdlib:db`, …, `gh:normalform-ai/clauz3-tools-assistant`).
- Each domain has a manifest at `tools/<domain>/clauz3.yaml`, validated against
  `src/clauz3/schemas/library.linkml.yaml` (with a generated JSON Schema). The
  loader is bounded and non-executable: `src/clauz3/libraries/manifest.py:42-97`.
- The installer copies **the whole `tools/<domain>/` directory**, not just
  `trusted/`: `copytree(src_domain, dst_domain, …)` at
  `src/clauz3/libraries/install.py:150-155`. Anything an author puts beside
  `trusted/` (the manifest, a README) is already installed.
- Provenance: `.clauz3-origin.json` (source and git revision) is written into
  each installed domain (`install.py:290-293`). `installed_libraries` groups
  installed domains back into libraries **by origin source**
  (`src/clauz3/libraries/discovery.py:192-218`).
- Bundled stdlib sources already use the package-root shape:
  `src/clauz3/stdlib/db/{README.md, tests/, tools/db/{clauz3.yaml, trusted/}}`.
  `clauz3 test stdlib:db` runs `tests/Justfile` from the wheel
  (`src/clauz3/libtest.py:47-113`).

### What was only proposed (not implemented)

- A `.clauz3/packages/<pkg>/` full-tree install with a symlinked
  `.clauz3/tools/<domain>` facade.
- Moving tests, examples and README under `tools/<domain>/`, so the domain
  directory is a complete library at source and when installed.
- Rewriting the folder structure during installation.

### Facts the analysis depends on

| Concern | Where it is decided | Unit |
| --- | --- | --- |
| What an install copies | `install.py:125-158` | domain directory |
| What gets fetched or cloned | `_resolve_tools_dir`, `install.py:319-343` | whole source repo |
| Which domains a source provides | `trusted_dirs`, `layout.py:88-122` (depth ≤ 2) | domain |
| Which domains a project shows as installed | `_domains`, `discovery.py:279-284` (unbounded `**/trusted`) | domain |
| Session selection and change detection | `WorkspaceLibraries.roots/fingerprint`, `workspace.py:91-122`; hashes the *whole domain dir* | domain |
| Update/remove review | `prepare`, `workspace.py:225-349`; per-domain file diffs, overlap refusal | domain |
| Grouping into "a library" | origin source in `.clauz3-origin.json` | source (package) |
| What agent code may import | `imports_policy.py:57-89, 125-141`; only `tools.<d>.trusted` prefixes derived from selected trusted roots | trusted root |
| What counts as a trusted effect | `policy_imports.py:130-140`; resolved file path under a trusted root | trusted root |
| Symlinks | rejected in `install.py:391-412`, `layout.py:125-139`, `workspace.py:28-56`, `manifest.py:93` | everywhere |
| Import roots and approval identity | `expand_import_roots`, `layout.py:211-233`; "an approval service authorizes the exact ordered roots" | project |

Importability and trust work independently of `sys.path`. The earlier claim that
tests "cannot become importable because the package root is not on
`sys.path`" was too broad. In the symlink proposal, `.clauz3/` *is* an import
root, so `.clauz3/packages/assistant/tests/...` is reachable by plain Python as
the `packages` namespace package. In the complete-domain option,
`.clauz3/tools/email/tests/` is reachable as `tools.email.tests`. In both
cases, two mechanisms keep agent programs from using these files:

- The import allowlist admits only `tools.<domain>.trusted…` prefixes for
  selected roots (`imports_policy.py:57-89`).
- Trust is decided by physical location under a selected trusted root
  (`policy_imports.py:130-140`; `ImportPolicy.trusted_roots`, line 73).

Copied tests and examples therefore never become trusted or agent-importable
under any of the options. Python import resolution does not tell the options
apart. Discovery, hashing and bookkeeping do.

---

## 2. The options as directory trees

The trees use the assistant library (`email` now, a hypothetical `calendar`
later) and autolabs (`lims` and `labop`, sharing one test suite).

### A. Current: package-root source, domain-subtree install

```
clauz3-tools-assistant/                 # source repo = package root
├── README.md  AGENTS.md  LICENSE
├── tests/
│   ├── Justfile                        # trusted := "../tools/email/trusted"
│   ├── test_effects.py                 # mocked subprocess runtime tests
│   └── cases/*_pass.py, *_fail.py
└── tools/email/
    ├── clauz3.yaml
    └── trusted/{effects.py, contracts.py}

consumer/.clauz3/                        # installed
├── tools/email/
│   ├── clauz3.yaml
│   ├── .clauz3-origin.json             # {"source": "gh:…/assistant", "revision": "<sha>"}
│   └── trusted/{effects.py, contracts.py}
└── skills/email/SKILL.md               # generated (with --skills)
```

### B. Complete domain library at source and install, copied unchanged

```
clauz3-tools-assistant/
├── README.md                           # repo overview only
└── tools/
    ├── email/
    │   ├── clauz3.yaml  README.md
    │   ├── trusted/{effects.py, contracts.py}
    │   ├── tests/{Justfile, test_effects.py, cases/}   # trusted := "../trusted", --import-root ../../..
    │   └── examples/…
    └── calendar/                       # hypothetical
        ├── clauz3.yaml                 # repeats gog + OAuth setup
        ├── trusted/…
        └── tests/{Justfile, cases/}    # second copy of runner boilerplate

consumer/.clauz3/tools/email/           # byte-identical to source tools/email/ + origin sidecar
```

Relative paths do keep parity: `../trusted`, with the import root three levels
up, resolves the same way in the source repo and in `.clauz3/`. That is a real
advantage of B over D.

### C. Top-level package, full-tree install, import mapping

```
clauz3-tools-assistant/
├── clauz3.yaml                          # package manifest: shared setup, domain list
├── README.md  tests/  examples/
└── tools/{email, calendar}/{clauz3.yaml, trusted/}

consumer/.clauz3/
├── packages/assistant/                  # full tree, one copy
│   ├── clauz3.yaml  README.md  tests/  examples/
│   └── tools/{email, calendar}/…
└── tools/email -> ../packages/assistant/tools/email      # C1: symlink facade
```

C2, without symlinks: add `.clauz3/packages/<pkg>` to the import roots for
each package and point trusted roots at
`packages/<pkg>/tools/<d>/trusted`. `tools` then becomes a namespace package
spread over N+1 directories.

### D. Install-time restructuring

```
source: as in A
consumer/.clauz3/tools/email/
├── clauz3.yaml  trusted/
└── tests/                              # copied from repo tests/, Justfile paths rewritten
```

Hybrid A+: A plus an optional repository-root `clauz3.yaml` holding only
shared, package-level metadata. It is described in §5 and needs no layout
change.

---

## 3. Comparison

### 3.1 Summary table

| Dimension | A (current) | B (complete domain) | C1 (symlink) | C2 (package roots) | D (rewrite) |
| --- | --- | --- | --- | --- | --- |
| Authored duplicate files | none | runner boilerplate per domain; shared setup prose per domain | none | none | none |
| Shared requirements | repeated per domain manifest (or A+) | repeated per domain | stated once | stated once | as A |
| Tests/examples in installed copy | no (reachable via origin) | yes | yes | yes | yes, altered |
| Source/install parity | yes (for what is installed) | yes, full | yes | yes | **no** |
| Mixed-domain suites (autolabs) | natural | must split or pick an owner | natural | natural | ambiguous owner |
| Discovery/overlap changes needed | none | **yes** (§3.4) | yes, symlink policy at 5+ sites | yes, roots and clash logic | yes |
| Approval/session identity | unchanged | fingerprint now covers tests/docs | changes | **import roots change** | fingerprint covers tests |
| Consumer repo size (installed copies are committed, `layout.py:35`) | small | +tests (db: 1.1 MB) | +everything | +everything | +tests |
| Windows / git without symlinks | fine | fine | fragile | fine | fine |
| Migration cost | 0 | medium | high | high | medium + ongoing |

### 3.2 Metadata ownership and duplication

A distinction matters here. A **duplicated authored file** is a fact a
maintainer must keep in sync by hand. **Multiple paths or bookkeeping** are
generated copies or pointers the tool maintains.

- A: the domain manifest is the only authored description. The installed
  manifest is a copy that tracks the installed code, which is deliberate: the
  installed description should match the installed code rather than a newer
  upstream one. The origin sidecar is generated provenance. The registry holds
  no descriptions. The one real overlap is the repository README/AGENTS.md
  restating parts of the manifest description. That is ordinary prose
  duplication, and it exists in every option.
- B: no new authored duplicates for one domain. A second domain sharing a
  backend repeats the tool and setup blocks (about 40 lines for gog in
  `clauz3-tools-assistant/tools/email/clauz3.yaml`) and the Justfile runner
  preamble. Splitting the autolabs `tests/Justfile` copies its runner
  variables into each domain.
- C: shared setup is written once in the package manifest. The cost is a new
  bookkeeping concept, "package versus enabled domains". Partly that concept
  already exists: sources are fetched whole (`install.py:319-343`), domains are
  selected individually, and the origin source groups them
  (`discovery.py:192-218`). C makes it explicit and adds a second on-disk unit.

### 3.3 Imports and trusted roots

Section 1 covers this: none of the options changes what agent programs may
import or what is trusted. The differences:

- A and B leave `expand_import_roots` (`<project>/.clauz3`, then `<project>`)
  untouched.
- C1 keeps the roots but has to relax five symlink refusals
  (`install.py:391`, `layout.py:125`, `workspace.py:28` and `:42`,
  `manifest.py:93`) without reopening the exfiltration hole that
  `_reject_symlinks` documents. Each relaxation needs a "symlink must stay
  inside `.clauz3/packages/` and be installer-created" rule. Paths resolve
  differently through a symlink: `policy_imports.py` resolves files before
  comparing them against roots, so trusted roots must also be stored resolved.
  Otherwise effects could silently stop being recognized as boundaries.
- C2 changes the ordered root list. That list is what an approval service
  authorizes (`layout.py:214-218`), and `check_project` clash detection would
  have to compare N directories, not two (`layout.py:186-208`).

### 3.4 A concrete hazard in B (and D)

Install and runtime use `trusted_dirs`, which is depth-limited and skips with a
warning. The project's installed-domain listing uses `_domains`, an unbounded
`tools.glob("**/trusted")` (`discovery.py:279-284`). This repository's own
examples carry `examples/<x>/tools/<x>/trusted/` (10 such directories). A
library that puts a similar example under `tools/email/examples/` would hit
this sequence:

1. Install would copy it (copytree takes the whole domain directory).
2. `installed_domains` would then report a phantom domain
   `email/examples/<x>/tools/<x>`.
3. The update review would refuse it: `overlaps("email/…", "email")` is true,
   which raises "Cannot replace overlapping library trees"
   (`workspace.py:59-61, 280-285`).

A test fixture named `trusted` directly under `tools/email/tests/` would be
worse. Its depth-2 domain `email/tests` *passes* `domain_problem` and would be
discovered as a real trusted layer by `trusted_dirs`. B therefore needs a rule
that discovery stops descending once a domain's `trusted/` is found, or that
`tests/` and `examples/` may not contain `trusted/` directories. Aligning
`_domains` with `trusted_dirs` is worth doing in any case.

Separately, `fingerprint` hashes the whole domain directory
(`workspace.py:108-122`), and `chat.py:990-995` uses it to detect "libraries
changed". Under B, a README or test edit would mark sessions as having changed
libraries. That fails safe but adds noise. The fix would be to narrow the hash
to `trusted/` plus the manifest, which is a trust-relevant change that needs its
own review.

### 3.5 Selected domains versus package installation

In every option the code's management unit is the domain: install target,
trusted root, fingerprint, review diff, removal, origin sidecar and generated
skill. Under A and B, metadata attached to the domain lines up with every
operation. C introduces a package that is installed whole while domains are
enabled selectively. It needs answers to "remove the last domain: does the
package go?" and "update one domain: does the other move to the new revision?"
Today the update flow already copes with multi-domain sources
(`workspace.py:251-258`), but each domain carries its own revision. C would have
to keep one revision per package.

### 3.6 Provenance, versioning, updates, removal

- A: the per-domain sidecar records source and revision. Updates diff a single
  domain tree. Removal deletes one directory.
- B: same mechanics, larger diffs. Test churn appears in the security review of
  an update (`workspace.py:290-315` lists added, removed and changed files),
  which dilutes attention on `trusted/` changes.
- C: one revision per package. Removing a domain means deleting a facade entry
  or a root, and the package directory may stay behind.
- D: an installed tree matches no revision of the source, so a diff against
  upstream needs the rewrite rules.

### 3.7 Author experience

- A: what `clauz3 test`, the `stdlib/*/tests/Justfile` files and both external
  repos already do. Authors write `tests/` beside `tools/` as in any Python
  repo.
- B: authors of single-domain repos get one self-contained folder. Multi-domain
  authors (autolabs) lose the shared suite and have to remember the
  no-nested-`trusted` rule.
- C: one manifest per package plus one per domain. Package concepts appear
  before a second consumer of them exists.

---

## 4. Keeping installed tests useful without executing or trusting them

The tests are valuable for two reasons. People and agents learn from the
pass/fail cases, and a user can check that the installed code behaves as
claimed. Neither requires copying tests into every consumer repository.

1. **Read, don't run.** Proof cases are plain Python showing an accepted or a
   refused program. For `gh:` sources, the content-addressed clone already
   exists after install (`install.py:332-333`). For stdlib, the cases ship in
   the wheel. The UI and the generated skill can link "Examples: `<source>`
   at `<revision>`, `tests/cases/`" using the origin sidecar. Showing them
   would be explicit, like reading a README, and would grant nothing.
2. **Run only on request.** `clauz3 test <source>` already exists
   (`libtest.py`). A small extension would accept an installed domain, resolve
   its origin to the recorded revision, and run that revision's
   `tests/Justfile`. Running tests is not purely static, so it must stay
   explicit. The assistant suite runs `python -m unittest` over
   `test_effects.py`, which imports and executes trusted modules with mocked
   `subprocess.run`. The db suite runs `setup_runtime_data.py`.
   `libtest.py:7-11` already says this. Nothing should run tests
   automatically on install, listing, or session start.
3. **Test the installed copy, not only the source.** To check the code the
   project will actually trust, a Justfile would have to accept the trusted
   root as a variable (`trusted := env_var_or_default("TRUSTED", …)`). That is
   a convention, not a layout change, and it works in A.
4. **Dependencies and siblings.** Runtime tests insert the repository root on
   `sys.path` (`test_effects.py`: `parents[1]`) and import
   `tools.email.trusted`. Cases import across domains only through their
   dependency's `tools.<dep>.trusted`, which the installer brings in through
   manifest `dependencies`. In A these assumptions hold at the repository root.
   In B the `parents[...]` depth changes. In D they break unless rewritten.
5. **Mixed-domain suites.** Autolabs has one `tests/Justfile` with separate
   `lims_prove`/`labop_prove` runners over 25 cases, with no case importing
   both domains today. In A, the suite belongs to the package. In B or D it
   must be split or given an arbitrary owning domain.
6. **Generated skills.** The skill renderer reads only `trusted/` and the
   manifest (`install.py:422-524`). It could append a short "Worked examples"
   pointer from the origin. It should not inline test bodies, which would make
   the skill a second copy that needs syncing.

---

## 5. Recommendation for this project

**Keep A. Adopt A+ (optional package manifest) only when a trigger fires.**

Reasons:

- Every management operation in the code is per-domain, and metadata at
  `tools/<domain>/` lines up with all of them. There is one authored
  description per domain and no sync burden beyond prose.
- The source/install relationship is simple to explain: "we install
  `tools/<domain>/`; everything else stays with the source, at the revision
  we recorded". The installed trust surface stays small and reviewable, and
  consumer repositories that commit `.clauz3/` stay small.
- It is the only option that needs no changes to symlink policy, import roots,
  discovery depth or fingerprint scope. Those are the trust-sensitive parts of
  the code.

**Strongest argument against A:** a consumer looking only at `.clauz3/` cannot
see proof cases or the long-form README, and an agent working offline loses
the best examples. The mitigations in §4 (origin-linked examples, explicit
`clauz3 test` of an installed domain) recover most of that value at small
cost. If users still keep asking for the full tree in the project, B for
single-domain libraries becomes reasonable. Fix §3.4 first.

**Migration cost from the current implementation:** none for A. The worthwhile
follow-ups, independent of layout:

1. Align `discovery._domains` with `layout.trusted_dirs` (depth limit, stop
   below an accepted `trusted/`). Add a test with a nested
   `examples/…/trusted`.
2. Optionally show origin and revision with an "examples and tests" link in
   the Libraries UI and the generated skill.
3. Optionally let `clauz3 test` take an installed domain, using its origin
   revision, with an explicit opt-in.

### A+: the package manifest, when needed

Triggers to revisit:

- A second domain in one repository shares an external tool or authentication
  (email + calendar via gog), and the setup blocks begin to differ.
- A user asks to install or remove a package's domains as a group.
- More than a handful of multi-domain external libraries exist.

Shape (sketch, not a spec):

```yaml
# clauz3-tools-assistant/clauz3.yaml      (repository root, optional)
schema_version: 1
kind: package
name: assistant
title: Personal assistant (Google via gog)
requirements:                              # shared by every domain listed below
  tools:
    - name: gog
      description: The gog CLI provides Gmail and Calendar access.
      documentation: https://gogcli.sh/quickstart.html
  setup:
    - id: google-oauth
      kind: authentication
      title: Authorize a Google account in gog
      ...
```

```yaml
# tools/email/clauz3.yaml                  (domain owns its own description)
schema_version: 1
name: email
title: Gmail
summary: …
requirements:
  setup:
    - id: gmail-scope
      kind: access
      title: Grant Gmail scope
      permissions: [Gmail authorization capable of creating drafts and sending]
      applies_to: [create_draft, send_email]
```

Responsibilities:

- The package manifest holds only what is shared. Domains still own their
  title, summary, description and effect-specific permissions.
- At install, the installer copies the package's shared requirements into each
  installed domain's manifest, or writes them alongside it. The installed
  domain stays self-describing, and nothing is installed outside
  `tools/<domain>/`. This is generated bookkeeping, like the origin sidecar,
  not an authored duplicate.
- The package manifest is not used for imports, trust, or a domain allowlist.
  The domain list remains whatever `trusted_dirs` discovers.

This avoids a new install tree, symlinks and import-root changes. If the
trigger never fires, nothing is lost.

### Options to avoid, briefly

- **C1 (symlink facade):** it reverses five fail-closed symlink refusals on
  the trust boundary, is fragile on Windows and with `core.symlinks=false`
  checkouts of committed `.clauz3/`, and adds package bookkeeping. Its benefit
  (one copy of tests in the consumer) is what A+ gets from the origin link.
- **C2 (per-package import roots):** it changes the approval identity of
  sessions and spreads the `tools` namespace across N roots. That is too much
  machinery for documentation convenience.
- **D (rewriting at install):** the installed tree matches no upstream
  revision, and rewriting Justfiles and `sys.path` depths is brittle forever.

## 6. Uncertainty

- I did not measure how often agents would benefit from local test cases
  versus the generated skill. If session transcripts show agents repeatedly
  failing in ways a pass/fail case would have prevented, that favours putting
  examples nearer the installed copy (still not tests in `trusted/`).
- Autolabs has no manifests yet. If its domains turn out to need the same
  setup, A+ arrives sooner.
- The fingerprint scope (whole domain directory) seems deliberately
  conservative. Narrowing it is a separate trust decision and not required by
  A.

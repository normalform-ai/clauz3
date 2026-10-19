# Registry

A manually-curated list of installable `clauz3` trusted-layer libraries
— the ones you pull in with `clauz3 install gh:org/repo` rather than
from `clauz3`'s own bundled `stdlib:` set.

This page is the inverse of the [Standard library tools](reference/stdlib.md)
page: stdlib lives inside the `clauz3` wheel and is always available;
registry entries are externally-hosted and installed on demand.

Gmail drafts and sending through gog now ship as [stdlib:google_workspace](stdlib/google_workspace.md).
The former assistant repository is no longer a catalogue source.

## Available libraries

Search sources with `clauz3 libraries email` or `clauz3 libraries autolabs`.
The registry contains source locations only. Bundled and installed libraries
provide their own [manifests](reference/library-manifests.md); external metadata
is cached during explicit installation or review. Once cached, domain names
and descriptions are searchable too. Listing never clones or executes code.
Private repositories require your Git credentials to have access.

| Library | Domain(s) | Visibility | Install |
|---|---|---|---|
| [clauz3-demo](https://github.com/normalform-ai/clauz3-demo) | Starter project — clones + bootstraps every bundled stdlib tool with sandbox-scoped contracts and five worked example plans. Best first-contact for a new agent. | **public** | `git clone git@github.com:normalform-ai/clauz3-demo.git && cd clauz3-demo && just bootstrap` |
| [clauz3-tools-autolabs](https://github.com/normalform-ai/clauz3-tools-autolabs) | `lims` — autonomous-lab effects (pipette, instrument scheduling, oligo ordering, plate movement) with relational + fluent contracts including a `dbtl_campaign` pattern. | **public** | `clauz3 install gh:normalform-ai/clauz3-tools-autolabs --skills` |

Pin to a tag, branch, or commit sha for reproducibility:

```bash
clauz3 install gh:normalform-ai/clauz3-tools-autolabs@v0.3.1
clauz3 install gh:normalform-ai/clauz3-tools-autolabs@<sha>
```

## Adding a library to the registry

Add the source reference to `src/clauz3/library_registry.yaml`. Keep descriptive
metadata, domain names, dependencies, and setup requirements in each library's
`tools/<domain>/clauz3.yaml`, not in the registry. Shared prerequisites may live
in an optional source-root `clauz3.yaml` with `kind: package`; installation
snapshots them beside each domain's manifest. Test manifest validation,
installation, and proofs before publishing. The table above is a human guide;
it is not consumed by the installer.

Legacy libraries without manifests remain installable, with a non-blocking
metadata warning in the CLI and installation review. New entries should provide
domain manifests. The root package manifest is optional, but declaring one
requires a manifest for every domain; malformed metadata is always an error.

## Status and future direction

The registry is intentionally simple: a source-only YAML index with library-owned manifests for
CLI, UI, and agent discovery.
There is no versioning, signing, or trust pipeline yet.
[Issue #47](https://github.com/cmungall/agent-deal/issues/47) tracks
the path to a signed/attested install model: a consumer's installed
layer would carry a manifest recording `source: gh:org/repo@<sha>` and
the proof receipt would name the attested version. At that point the
registry can grow a notion of "current pin" per library and serve as a
real distribution surface rather than a directory.

If the registry ever moves beyond hand curation, Google's Agentic Resource
Discovery spec is the candidate discovery layer: a trusted-layer publisher
would advertise its layers from its own domain, and the registry (or
`clauz3 install`) could cross-check a `gh:org/repo` origin against that
catalog. That is evaluated in
[Background](explanation/background.md#agentic-resource-discovery-ard) and
tracked in [issue #86](https://github.com/cmungall/agent-deal/issues/86);
nothing is implemented yet.

The [registry certification proposal](todos/registry-certification.md)
sketches how third parties (agents and humans) could attest to a layer's
correctness and contract vocabulary, and how consumers could require those
attestations.

For the broader bud-off plan that motivates having external trusted-layer
repos at all, see [issue #41](https://github.com/cmungall/agent-deal/issues/41).

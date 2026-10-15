# Registry

A manually-curated list of installable `clauz3` trusted-layer libraries
— the ones you pull in with `clauz3 install gh:org/repo` rather than
from `clauz3`'s own bundled `stdlib:` set.

This page is the inverse of the [Standard library tools](reference/stdlib.md)
page: stdlib lives inside the `clauz3` wheel and is always available;
registry entries are externally-hosted and installed on demand.

## Available libraries

Search the installable entries from the CLI or the agent's read-only library
tool with `clauz3 libraries email` or `clauz3 libraries lab`. The catalog is
packaged with ClauZ3 and searched offline; discovery does not clone or execute
code. Private libraries still require repository access at installation time.

| Library | Domain(s) | Visibility | Install |
|---|---|---|---|
| [clauz3-demo](https://github.com/normalform-ai/clauz3-demo) | Starter project — clones + bootstraps every bundled stdlib tool with sandbox-scoped contracts and five worked example plans. Best first-contact for a new agent. | **public** | `git clone git@github.com:normalform-ai/clauz3-demo.git && cd clauz3-demo && just bootstrap` |
| [clauz3-tools-autolabs](https://github.com/normalform-ai/clauz3-tools-autolabs) | `lims` — autonomous-lab effects (pipette, instrument scheduling, oligo ordering, plate movement) with relational + fluent contracts including a `dbtl_campaign` pattern. | **public** | `clauz3 install gh:normalform-ai/clauz3-tools-autolabs --skills` |
| `clauz3-tools-assistant` | `email` — outbound mail via `gog gmail send` with the load-bearing `dry_run_only()` safety pattern; calendar/tasks planned. | **private**, available on request from the maintainers | `clauz3 install gh:normalform-ai/clauz3-tools-assistant --skills` *(requires access; uses your existing git SSH auth)* |

Pin to a tag, branch, or commit sha for reproducibility:

```bash
clauz3 install gh:normalform-ai/clauz3-tools-autolabs@v0.3.1
clauz3 install gh:normalform-ai/clauz3-tools-autolabs@<sha>
```

## Adding a library to the registry

Open a PR updating `docs/registry.md` and the machine-readable
`src/clauz3/library_catalog.json`. Manual curation only — there is
no auto-discovery. The bar is loose: a trusted-layer repo that's been
exercised against `clauz3 install` and `clauz3 prove` is enough. New
entries should follow the table shape above and link to the repo (or
note "available on request" if private).

## Status and future direction

The registry is intentionally simple: a hand-curated table with packaged JSON
metadata for CLI and agent discovery.
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

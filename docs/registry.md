# Registry

A manually-curated list of installable `clauz3` trusted-layer libraries
— the ones you pull in with `clauz3 install gh:org/repo` rather than
from `clauz3`'s own bundled `stdlib:` set.

This page is the inverse of the [Standard library tools](reference/stdlib.md)
page: stdlib lives inside the `clauz3` wheel and is always available;
registry entries are externally-hosted and installed on demand.

## Available libraries

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

Open a PR against `docs/registry.md`. Manual curation only — there is
no auto-discovery. The bar is loose: a trusted-layer repo that's been
exercised against `clauz3 install` and `clauz3 prove` is enough. New
entries should follow the table shape above and link to the repo (or
note "available on request" if private).

## Status and future direction

The registry is intentionally simple: a hand-curated markdown table.
There is no versioning, signing, or trust pipeline yet.
[Issue #47](https://github.com/cmungall/agent-deal/issues/47) tracks
the path to a signed/attested install model: a consumer's installed
layer would carry a manifest recording `source: gh:org/repo@<sha>` and
the proof receipt would name the attested version. At that point the
registry can grow a notion of "current pin" per library and serve as a
real distribution surface rather than a directory.

For the broader bud-off plan that motivates having external trusted-layer
repos at all, see [issue #41](https://github.com/cmungall/agent-deal/issues/41).

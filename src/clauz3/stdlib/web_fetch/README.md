# web_fetch

A trusted `clauz3` layer for HTTP GET, with URL-prefix policies, fetch-count
bounds, and exfiltration-style content guards on the URL itself.

Unlike the layers under `examples/`, this effect is **real**, not a mock:
`fetch_url` actually issues an HTTP request using `urllib.request` from
the standard library. The prover never runs the body — it records each
call as a fact and proves the contract. The body runs only under
`clauz3 run`, after a program is proved and approved.

## Install

```bash
clauz3 install stdlib:web_fetch
```

## Effects

Import with `from tools.web_fetch.trusted.effects import fetch_url`:

- `fetch_url(url: str) -> str` — issue an HTTP GET and return the response
  body as UTF-8 text (with `errors='replace'`). Precondition: `url` starts
  with `http://` or `https://`. Recorded under the `net` and `fetch` markers.
  30-second timeout.

## Contracts

Import with `from tools.web_fetch.trusted import contracts as web`:

- `web.no_guarantees()` — explicit null contract.
- `web.no_fetches()` — the program issues no HTTP fetches.
- `web.only_fetch_under(prefix)` — every fetched URL starts with `prefix`.
- `web.never_fetch_under(prefix)` — no fetched URL starts with `prefix`.
- `web.https_only()` — every fetched URL uses HTTPS.
- `web.fetches_at_most(count)` — at most `count` fetches occur.
- `web.no_url_contains(substring)` — no fetched URL contains `substring`
  (exfil guard).
- `web.url_length_at_most(max_chars)` — every fetched URL is at most
  `max_chars` long.

`only_fetch_under` / `never_fetch_under` are URL-prefix policies compiled to
`str.startswith` over the symbolic URL argument; use fully-qualified prefixes
(`"https://api.github.com/repos/"`) for tight scoping.

## The exfil-guard angle

`no_url_contains` is the unusual contract here. Static substring guards on
agent-built URLs let you prove, before any request fires, that a fetched URL
cannot smuggle local data into the query string. For an agent that's been
given a local secret to compute against, this is the cheapest way to ensure
the secret doesn't leak via an outbound HTTP call.

## Example

```python
import clauz3
from tools.web_fetch.trusted import contracts as web
from tools.web_fetch.trusted.effects import fetch_url


@clauz3.guarantee(web.https_only())
@clauz3.guarantee(web.only_fetch_under("https://api.github.com/repos/"))
@clauz3.guarantee(web.fetches_at_most(5))
@clauz3.guarantee(web.no_url_contains("token="))
def main() -> None:
    fetch_url("https://api.github.com/repos/normalform-ai/clauz3")
```

## Tests

```bash
clauz3 test stdlib:web_fetch
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not.

The test suite is proof-only — it never actually hits the network.

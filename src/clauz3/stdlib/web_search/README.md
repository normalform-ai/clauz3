# web_search

A trusted `clauz3` layer for web search with query-length, query-count, and
substring-based privacy / exfil guards. The backend search service is
configurable; the stdlib does not pick a vendor.

Unlike the layers under `examples/`, this effect is **real**, not a mock:
`web_search` actually POSTs the query to a JSON endpoint. The prover never
runs the body — it records each call as a fact and proves the contract.
The body runs only under `clauz3 run`, after a program is proved and approved.

## Install

```bash
clauz3 install stdlib:web_search
```

## Configuring the backend

Set two environment variables before running a proved program that calls
`web_search`:

- `CLAUZ3_SEARCH_URL` — JSON endpoint that accepts `{"query": str, "key": str}`
  via POST and returns a JSON array of result URL strings.
- `CLAUZ3_SEARCH_API_KEY` — optional API key forwarded in the JSON body
  (empty if unset).

If `CLAUZ3_SEARCH_URL` is unset, the body raises `RuntimeError`. This means
test cases (which exercise only the prover, not the body) work without any
backend configured.

## Effects

Import with `from tools.web_search.trusted.effects import web_search`:

- `web_search(query: str) -> list[str]` — return a list of result URLs.
  Precondition: `query` is non-empty. Recorded under the `net` and `search`
  markers.

## Contracts

Import with `from tools.web_search.trusted import contracts as srch`:

- `srch.no_guarantees()` — explicit null contract.
- `srch.no_searches()` — the program issues no searches.
- `srch.searches_at_most(count)` — at most `count` searches occur.
- `srch.query_length_at_most(max_chars)` — every query is at most
  `max_chars` long.
- `srch.no_query_contains(substring)` — no query contains `substring`
  (privacy / exfil guard).

## Example

```python
import clauz3
from tools.web_search.trusted import contracts as srch
from tools.web_search.trusted.effects import web_search


@clauz3.guarantee(srch.searches_at_most(3))
@clauz3.guarantee(srch.query_length_at_most(100))
@clauz3.guarantee(srch.no_query_contains("BEGIN PRIVATE KEY"))
def main() -> None:
    web_search("clauz3 static contracts")
```

## Tests

```bash
clauz3 test stdlib:web_search
```

`tests/cases/*_pass.py` must prove; `tests/cases/*_fail.py` must not.

The test suite is proof-only — it never hits the configured backend and
does not require `CLAUZ3_SEARCH_URL` to be set.

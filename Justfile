set shell := ["bash", "-cu"]

example-justfiles := `find examples -mindepth 2 -maxdepth 2 -name Justfile | sort | paste -sd' ' -`
example-roots := `find examples -mindepth 1 -maxdepth 1 -type d | sort | paste -sd: -`
fixture-roots := `find tests/fixtures -mindepth 1 -maxdepth 1 -type d | sort | paste -sd: -`
stdlib-justfiles := `find src/clauz3/stdlib -name Justfile | sort | paste -sd' ' -`

test: pytest check examples stdlib

pytest:
    uv run pytest

check:
    uv run ruff check .
    uv run ruff format --check .
    MYPYPATH="src:{{example-roots}}:{{fixture-roots}}" uv run mypy src tests examples

examples:
    for justfile in {{example-justfiles}}; do \
        just -f "$justfile" test; \
    done

stdlib:
    for justfile in {{stdlib-justfiles}}; do \
        just -f "$justfile" test; \
    done

docs:
    uv run --group docs mkdocs build --strict

serve-docs port="8077":
    uv run --group docs mkdocs serve --dev-addr 127.0.0.1:{{port}}

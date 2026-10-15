set shell := ["bash", "-cu"]

example-justfiles := `find examples -mindepth 2 -maxdepth 2 -name Justfile | sort | paste -sd' ' -`
example-roots := `find examples -mindepth 1 -maxdepth 1 -type d | sort | paste -sd: -`
fixture-roots := `find tests/fixtures -mindepth 1 -maxdepth 1 -type d | sort | paste -sd: -`
stdlib-justfiles := `find src/clauz3/stdlib -name Justfile | sort | paste -sd' ' -`

test: pytest check node-test examples stdlib

# Install a user-wide clauz3 command backed by this checkout's source.
install-editable:
    uv tool install --editable .

# Refresh the editable tool and its dependencies after package metadata changes.
reinstall:
    uv tool install --editable . --reinstall

# One worker per core; tests stay isolated because each worker is its own process.
pytest:
    uv run pytest -n auto

web-assets := "src/clauz3/sessions/web src/clauz3/approvals/web"

# Syntax-check the web scripts and run their node unit tests.
node-test:
    for script in `find {{web-assets}} -name '*.js' | sort`; do \
        node --check "$script" || exit "$?"; \
    done
    node --test tests/web/*.test.mjs

# The fast CI tier for changes that touch only web assets.
ui-test: node-test
    uv run pytest -n auto tests/test_workspace.py tests/test_workspace_files.py tests/test_workspace_libraries.py tests/test_approval_profiles.py tests/test_start.py

differential runs="8" rows="3":
    uv run python -m clauz3.differential --root . --runs {{runs}} --max-rows {{rows}}

# The explicit marker overrides pyproject.toml's default corpus exclusion.
differential-test:
    uv run pytest -s -m differential_corpus tests/test_differential_corpus.py

check:
    uv run ruff check .
    uv run ruff format --check .
    MYPYPATH="src:{{example-roots}}:{{fixture-roots}}" uv run mypy src tests examples
    uv run python -m clauz3.libraries.contract_schema --output docs/reference/contracts.linkml.json --check

# Regenerate the LinkML catalog without importing the trusted libraries.
contract-schema:
    uv run python -m clauz3.libraries.contract_schema --output docs/reference/contracts.linkml.json

examples:
    for justfile in {{example-justfiles}}; do \
        just -f "$justfile" test || exit "$?"; \
    done

stdlib:
    for justfile in {{stdlib-justfiles}}; do \
        just -f "$justfile" test || exit "$?"; \
    done

docs:
    uv run --group docs mkdocs build --strict

serve-docs port="8077":
    uv run --group docs mkdocs serve --dev-addr 127.0.0.1:{{port}}

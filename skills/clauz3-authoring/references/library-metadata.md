# Library metadata without checkout-relative links

The main skill includes a domain manifest example and package layout. This
reference explains where the authoritative schema lives and how to validate
against the Clauz3 version you are actually using.

Clauz3 ships its LinkML schema and generated JSON Schemas as package resources.
From a Python environment containing Clauz3, read the authoritative LinkML with:

```bash
python -c 'from importlib.resources import files; print((files("clauz3") / "schemas/library.linkml.yaml").read_text())'
```

The same directory contains `library.schema.json` and `package.schema.json`.
If using `uv`, run the command with `uv run python` from the environment where
Clauz3 is installed. Do not assume a consumer project has `docs/` or a Clauz3
source checkout. An older distribution missing these resources needs an upgrade
to use this manifest format; do not invent a replacement schema.

Validate a domain or package manifest explicitly:

```bash
python -m clauz3.libraries.manifest tools/bio/clauz3.yaml
python -m clauz3.libraries.manifest clauz3.yaml
```

Then test installation into a temporary consumer project with
`clauz3 install /path/to/library --into /path/to/test-project --skills`.
Installation validates all domain manifests and merged requirements before
copying. Inspect the resulting library record and generated skill; descriptions,
setup steps, dependencies, and source provenance should describe that copy.
Neither validation nor installation executes setup instructions or verifies
credentials.

`tools/<domain>/clauz3.yaml` owns the exact domain `name`, human `title`,
`summary`, `description`, optional dependency source references, and requirements.
A namespaced domain uses a slash, e.g. `google_workspace/gmail`; imports use dots.
An optional root `clauz3.yaml` with `kind: package` owns shared requirements and
the bundle title, not a duplicate domain inventory. Installing a package requires
every domain to have its own manifest. Keep Python dependencies, executables,
and manual authentication/configuration steps separate. Never include secrets.

For the full reference in a matching source checkout, the file is
`docs/reference/library-manifests.md`. That path is relative to the Clauz3
repository, not to an installed copy of this skill. The schema resource above
remains available without that documentation tree.

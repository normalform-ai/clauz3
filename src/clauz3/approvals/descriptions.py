"""Shared, escaped rendering of guarantees and independently verified identity."""

import re
from collections.abc import Mapping
from html import escape


def render_guarantees(record: Mapping[str, object]) -> str:
    guarantees = record.get("guarantees")
    docs = record.get("contract_documentation")
    documentation = docs if isinstance(docs, dict) else {}
    generated = record.get("contract_descriptions")
    descriptions = generated if isinstance(generated, dict) else {}
    identities = record.get("contract_identities")
    identities = identities if isinstance(identities, dict) else {}
    if not isinstance(guarantees, list) or not guarantees:
        return "<li>None declared.</li>"
    items = []
    for index, expression in enumerate(guarantees):
        original = str(expression)
        doc = documentation.get(original)
        details = descriptions.get(original)
        sentence = details.get("text") if isinstance(details, dict) else None
        raw_identity = identities.get(original)
        identity = raw_identity if isinstance(raw_identity, dict) else {}
        resolved = isinstance(identity.get("symbol"), str) and isinstance(
            identity.get("call"), str
        )
        parts = []
        if resolved:
            title = escape(
                str(identity.get("title") or identity.get("library") or "Library")
            )
            badge = {"mock": "🧪 Mock", "mixed": "🧪 Mixed"}.get(
                str(identity.get("mock"))
            )
            parts.append(
                f'<p class="contract-library">{title}'
                + (f' <span class="contract-mock">{badge}</span>' if badge else "")
                + "</p>"
            )
        call = str(identity["call"]) if resolved else original
        parts.append(f'<code class="contract-expression">{escape(call)}</code>')
        if not isinstance(sentence, str) or not sentence:
            sentence = (
                re.split(r"\n\s*\n", doc.strip(), maxsplit=1)[0]
                if isinstance(doc, str) and doc.strip()
                else None
            )
        if sentence:
            parts.append(f'<p class="contract-description">{escape(sentence)}</p>')
        if resolved:
            parts.append(
                '<p class="contract-symbol">Resolved contract: <code>'
                + escape(str(identity["symbol"]))
                + "</code></p>"
            )
        else:
            parts.append(
                '<p class="contract-identity-unavailable">'
                "Resolved identity not recorded.</p>"
            )
        expanded = []
        if resolved:
            expanded.append(
                f"<p>Original expression: <code>{escape(original)}</code></p>"
            )
            for label, key in (
                ("Library namespace", "library"),
                ("Recorded library origin", "origin"),
                ("Recorded revision", "revision"),
                ("Source file", "source_file"),
                ("Source line", "source_line"),
                ("Source file SHA-256", "source_sha256"),
                ("Verified layer SHA-256", "layer_sha256"),
            ):
                value = identity.get(key)
                if value:
                    expanded.append(
                        f'<p class="contract-provenance">{label}: '
                        f"<code>{escape(str(value))}</code></p>"
                    )
            if code := identity.get("source_code"):
                expanded.append(
                    "<p>Contract source at verification</p>"
                    f'<pre class="contract-source">{escape(str(code))}</pre>'
                )
        if isinstance(doc, str) and doc:
            expanded.append(
                '<p class="contract-documentation" style="white-space: pre-wrap">'
                + escape(doc)
                + "</p>"
            )
        if expanded:
            parts.append(
                f'<details class="contract-details" data-panel="contract-{index}">'
                "<summary>Source and documentation</summary>"
                + "".join(expanded)
                + "</details>"
            )
        items.append("<li>" + "".join(parts) + "</li>")
    return "\n".join(items)

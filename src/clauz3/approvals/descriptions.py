"""Shared, escaped rendering of guarantees and trusted documentation."""

from collections.abc import Mapping
from html import escape


def render_guarantees(record: Mapping[str, object]) -> str:
    guarantees = record.get("guarantees")
    docs = record.get("contract_documentation")
    documentation = docs if isinstance(docs, dict) else {}
    generated = record.get("contract_descriptions")
    descriptions = generated if isinstance(generated, dict) else {}
    if not isinstance(guarantees, list) or not guarantees:
        return "<li>None declared.</li>"
    items = []
    for index, expression in enumerate(guarantees):
        text = str(expression)
        doc = documentation.get(text)
        details = descriptions.get(text)
        sentence = details.get("text") if isinstance(details, dict) else None
        code = f"<code>{escape(text)}</code>"
        has_sentence = isinstance(sentence, str) and bool(sentence)
        summary = (
            f'<p class="contract-description">{escape(str(sentence))}</p>'
            if has_sentence
            else code
        )
        documentation_html = (
            '<p class="contract-documentation" style="white-space: pre-wrap">'
            f"{escape(doc)}</p>"
            if isinstance(doc, str) and doc
            else ""
        )
        detail_content = (code if has_sentence else "") + documentation_html
        disclosure = (
            f'<details class="contract-details" data-panel="contract-{index}">'
            f"<summary>Details</summary>{detail_content}</details>"
            if detail_content
            else ""
        )
        items.append(f"<li>{summary}{disclosure}</li>")
    return "\n".join(items)

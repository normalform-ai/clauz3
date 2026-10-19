/* Render historical, independently verified identity as inert text. */
window.clauz3RenderGuarantees = (record) => {
  const text = (tag, value, cls) => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (cls) node.className = cls;
    return node;
  };
  const mapping = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const generated = record.contract_descriptions;
  const documentation = record.contract_documentation;
  const identities = record.contract_identities;
  const guarantees = text("ul", "", "profile-guarantees");
  for (const guarantee of Array.isArray(record.guarantees) ? record.guarantees : []) {
    const item = text("li", "");
    const identity = mapping(identities) ? identities[guarantee] : null;
    const resolved = mapping(identity) && typeof identity.symbol === "string" && typeof identity.call === "string";
    const doc = mapping(documentation) ? documentation[guarantee] : null;
    let sentence = mapping(generated) ? generated[guarantee]?.text : null;
    if (typeof sentence !== "string" || !sentence) sentence = typeof doc === "string" ? doc.trim().split(/\n\s*\n/, 1)[0] : "";
    if (resolved) {
      const library = text("p", identity.title || identity.library || "Library", "contract-library");
      const badge = { mock: "🧪 Mock", mixed: "🧪 Mixed" }[identity.mock];
      if (badge) library.append(text("span", badge, "contract-mock"));
      item.append(library);
    }
    item.append(text("code", resolved ? identity.call : guarantee, "contract-expression"));
    if (sentence) item.append(text("p", sentence, "contract-description"));
    if (resolved) {
      const symbol = text("p", "Resolved contract: ", "contract-symbol");
      symbol.append(text("code", identity.symbol));
      item.append(symbol);
    } else item.append(text("p", "Resolved identity not recorded.", "contract-identity-unavailable"));
    const disclosure = text("details", "", "contract-details");
    disclosure.dataset.panel = `contract-${guarantees.children.length}`;
    disclosure.append(text("summary", "Source and documentation"));
    if (resolved) {
      const original = text("p", "Original expression: ");
      original.append(text("code", guarantee));
      disclosure.append(original);
      for (const [label, key] of [["Library namespace", "library"], ["Recorded library origin", "origin"], ["Recorded revision", "revision"], ["Source file", "source_file"], ["Source line", "source_line"], ["Source file SHA-256", "source_sha256"], ["Verified layer SHA-256", "layer_sha256"]]) {
        if (!identity[key]) continue;
        const field = text("p", `${label}: `, "contract-provenance");
        field.append(text("code", String(identity[key])));
        disclosure.append(field);
      }
      if (identity.source_code) disclosure.append(text("p", "Contract source at verification"), text("pre", identity.source_code, "contract-source"));
    }
    if (typeof doc === "string" && doc) {
      const paragraph = text("p", doc, "contract-documentation");
      paragraph.style.whiteSpace = "pre-wrap";
      disclosure.append(paragraph);
    }
    if (disclosure.children.length > 1) item.append(disclosure);
    guarantees.append(item);
  }
  if (!guarantees.children.length) guarantees.append(text("li", "No contract details recorded."));
  return guarantees;
};

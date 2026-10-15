/* Render saved, independently verified metadata as text, never as HTML. */
window.clauz3RenderGuarantees = (record) => {
  const text = (tag, value, cls) => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (cls) node.className = cls;
    return node;
  };
  const mapping = (value) =>
    value !== null && typeof value === "object" && !Array.isArray(value);
  const generated = record.contract_descriptions;
  const documentation = record.contract_documentation;
  const guarantees = text("ul", "", "profile-guarantees");
  for (const guarantee of Array.isArray(record.guarantees)
    ? record.guarantees
    : []) {
    const item = text("li", "");
    const sentence = mapping(generated) ? generated[guarantee]?.text : null;
    const hasSentence = typeof sentence === "string" && Boolean(sentence);
    item.append(
      hasSentence
        ? text("p", sentence, "contract-description")
        : text("code", guarantee),
    );
    const disclosure = text("details", "", "contract-details");
    disclosure.dataset.panel = `contract-${guarantees.children.length}`;
    disclosure.append(text("summary", "Details"));
    if (hasSentence) disclosure.append(text("code", guarantee));
    const doc = mapping(documentation) ? documentation[guarantee] : null;
    if (typeof doc === "string" && doc) {
      const paragraph = text("p", doc, "contract-documentation");
      paragraph.style.whiteSpace = "pre-wrap";
      disclosure.append(paragraph);
    }
    if (disclosure.children.length > 1) item.append(disclosure);
    guarantees.append(item);
  }
  if (!guarantees.children.length)
    guarantees.append(text("li", "No contract details recorded."));
  return guarantees;
};

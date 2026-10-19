import assert from "node:assert/strict";
import { test } from "node:test";

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.style = {};
  }
  append(...children) { this.children.push(...children); }
}
globalThis.window = {};
globalThis.document = { createElement: tag => new Element(tag) };
await import("../../src/clauz3/approvals/web/guarantees.js");
const render = window.clauz3RenderGuarantees;

test("resolved identity leads, with original expression and source in expandable details", () => {
  const expression = "rules.at_most(1)";
  const identity = {
    title: "Mock email", library: "email", mock: "mock",
    call: "email.at_most(count=1)", symbol: "tools.email.trusted.contracts.at_most",
    source_file: "/library/<script>/contracts.py", source_line: 12,
    source_code: 'def at_most(count):\n    """<script>inert</script>"""',
    source_sha256: "abc123", layer_sha256: "def456", origin: "stdlib:email",
  };
  const list = render({
    guarantees: [expression],
    contract_identities: { [expression]: identity },
    contract_descriptions: { [expression]: { text: "At most one simulated email." } },
    contract_documentation: { [expression]: "Sends no real email." },
  });
  const [library, code, description, symbol, details] = list.children[0].children;
  assert.equal(library.textContent, "Mock email");
  assert.equal(library.children[0].textContent, "🧪 Mock");
  assert.equal(code.tagName, "code");
  assert.equal(code.textContent, "email.at_most(count=1)");
  assert.equal(description.textContent, "At most one simulated email.");
  assert.equal(symbol.children[0].textContent, identity.symbol);
  assert.equal(details.tagName, "details");
  assert.equal(details.children[0].textContent, "Source and documentation");
  assert.equal(details.children[1].children[0].textContent, expression);
  assert.equal(details.children.find(node => node.tagName === "pre").textContent, identity.source_code);
  assert.equal(details.children.at(-1).textContent, "Sends no real email.");
  assert.ok(details.children.some(node => node.children.some(child => child.textContent === identity.source_file)));
});

test("docstring first paragraph is a visible fallback, with full scope in details", () => {
  const doc = "No files change.\nThis includes deletion.\n\nOther libraries are not covered.";
  const item = render({
    guarantees: ["fs.read_only()"],
    contract_documentation: { "fs.read_only()": doc },
  }).children[0];
  assert.equal(item.children[1].textContent, "No files change.\nThis includes deletion.");
  assert.equal(item.children.at(-1).children.at(-1).textContent, doc);
});

test("legacy expressions remain inert and missing identity is explicit", () => {
  const expression = "mail.only_recipients(['<script>'])";
  const item = render({ guarantees: [expression] }).children[0];
  assert.equal(item.children.length, 2);
  assert.equal(item.children[0].textContent, expression);
  assert.equal(item.children[1].textContent, "Resolved identity not recorded.");
});

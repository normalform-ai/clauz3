import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(
  new URL("../../src/clauz3/sessions/web/signed-out.js", import.meta.url),
  "utf8",
);

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attributes = {};
    this.textContent = "";
  }
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.children = children;
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
  get text() {
    return [
      this.textContent,
      ...this.children.map((child) => (typeof child === "string" ? child : child.text)),
    ].join("");
  }
}

// A page with a stale conversation and a pending approval card in it.
function load(statuses) {
  const body = new Element("body");
  const card = new Element("article");
  card.textContent = "Action review · Your approval needed";
  body.append(card);
  const document = {
    title: "Clauz3 · Workspace",
    body,
    createElement: (tag) => new Element(tag),
  };
  const requests = [];
  const fetch = async (url) => {
    requests.push(url);
    return { status: statuses.shift() };
  };
  const context = { document, fetch };
  context.globalThis = context;
  vm.runInNewContext(source, context);
  return { api: context.clauz3SignedOut, document, fetch: context.fetch, requests };
}

test("only a missing or stale sign-in counts as signed out", () => {
  const { api } = load([]);
  assert.equal(api.response(401), true);
  for (const status of [200, 403, 404, 409, 500]) assert.equal(api.response(status), false);
  assert.equal(api.close(1008), true);
  for (const code of [1000, 1006, 1013, 4001]) assert.equal(api.close(code), false);
});

test("a 401 from any request replaces the page with one signed-out message", async () => {
  const { api, document, fetch, requests } = load([200, 401]);
  assert.equal((await fetch("/ui/rows")).status, 200);
  assert.equal(api.active, false);
  assert.match(document.body.text, /Your approval needed/);
  assert.equal((await fetch("/api/chat/conversations")).status, 401);
  assert.deepEqual(requests, ["/ui/rows", "/api/chat/conversations"]);
  assert.equal(api.active, true);
  assert.equal(document.title, "Clauz3 · Signed out");
  assert.equal(document.body.children.length, 1);
  const [page] = document.body.children;
  assert.equal(page.className, "signed-out");
  assert.equal(page.attributes.role, "alert");
  assert.doesNotMatch(document.body.text, /Your approval needed/);
  assert.match(document.body.text, /^This tab is signed out/);
  assert.match(document.body.text, /server restarts/);
  assert.match(document.body.text, /Run clauz3 login-url in a terminal/);
  assert.match(document.body.text, /cannot be approved from this tab/);
  assert.equal(
    page.children.flatMap((p) => p.children || []).find((c) => c.tag === "code").textContent,
    "clauz3 login-url",
  );
  assert.doesNotMatch(document.body.text, /key=/);
});

test("entering twice keeps a single message", () => {
  const { api, document } = load([]);
  api.enter(document);
  const [page] = document.body.children;
  api.enter(document);
  assert.deepEqual(document.body.children, [page]);
});

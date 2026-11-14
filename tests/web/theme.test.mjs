import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

class Element {
  constructor() {
    this.listeners = {};
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
  }
  addEventListener(name, callback) {
    this.listeners[name] = callback;
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
  matches() {
    return false;
  }
  showPopover() {}
  hidePopover() {}
  getBoundingClientRect() {
    return { left: 10, bottom: 30, top: 10 };
  }
  querySelector(selector) {
    const all = [
      ...this.children,
      ...this.children.flatMap((child) => child.children || []),
    ];
    if (selector.includes('aria-checked="true"'))
      return (
        all.find(
          (el) =>
            el.attributes?.["aria-checked"] === "true" &&
            !el.hidden &&
            !el.disabled,
        ) || null
      );
    return all.find((el) => !el.hidden && !el.disabled) || null;
  }
  focus() {
    this.focused = true;
  }
}

const KNOWN_IDS = [
  "theme-picker",
  "theme-menu",
  "theme-options",
  "theme-current",
];

function fixture(initialStored = "", presentIds = KNOWN_IDS) {
  const nodes = new Map(presentIds.map((id) => [id, new Element()]));
  const menu = nodes.get("theme-menu");
  const options = nodes.get("theme-options");
  if (menu && options) menu.append(options);
  const store = new Map(initialStored ? [["clauz3-theme", initialStored]] : []);
  const document = new Element();
  document.documentElement = new Element();
  document.getElementById = (id) => nodes.get(id) || null;
  document.createElement = () => new Element();
  const localStorage = {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => store.set(key, String(value)),
  };
  const context = vm.createContext({
    document,
    localStorage,
    innerWidth: 1024,
    innerHeight: 768,
  });
  context.globalThis = context;
  vm.runInContext(
    readFileSync(
      new URL("../../src/clauz3/sessions/web/menus.js", import.meta.url),
      "utf8",
    ),
    context,
  );
  return {
    nodes,
    document,
    store,
    theme: context.clauz3Theme,
    menuApi: context.clauz3Menu,
  };
}

test("index.html includes the required theme switcher element ids", () => {
  const html = readFileSync(
    new URL("../../src/clauz3/sessions/web/index.html", import.meta.url),
    "utf8",
  );
  for (const id of KNOWN_IDS) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
});

test("initializes with default parchment theme and renders all preset options", () => {
  const { nodes, document, theme } = fixture();
  assert.equal(document.documentElement.dataset.theme, "parchment");
  assert.equal(nodes.get("theme-current").textContent, "Parchment");
  const options = nodes.get("theme-options").children;
  assert.equal(options.length, theme.THEMES.length);
  assert.equal(options[0].attributes["aria-checked"], "true");
  assert.equal(options[0].children[2].textContent, "✓");
  assert.equal(options[1].attributes["aria-checked"], "false");
});

test("restores saved theme from localStorage and falls back safely on unknown values", () => {
  const saved = fixture("obsidian");
  assert.equal(saved.document.documentElement.dataset.theme, "obsidian");
  assert.equal(saved.nodes.get("theme-current").textContent, "Obsidian");

  const unknown = fixture("nonexistent-theme");
  assert.equal(unknown.document.documentElement.dataset.theme, "parchment");
});

test("missing picker elements still apply the root theme without throwing", () => {
  const { document } = fixture("swiss", []);
  assert.equal(document.documentElement.dataset.theme, "swiss");
});

test("clicking an option or pressing arrow keys switches and persists the theme, and opening focuses the checked option", () => {
  const { nodes, document, store } = fixture();
  const options = nodes.get("theme-options").children;
  const indigoButton = options.find((b) => b.dataset.theme === "indigo");
  indigoButton.listeners.click();
  assert.equal(document.documentElement.dataset.theme, "indigo");
  assert.equal(store.get("clauz3-theme"), "indigo");
  assert.equal(nodes.get("theme-current").textContent, "Formal Indigo");
  assert.equal(indigoButton.attributes["aria-checked"], "true");

  nodes.get("theme-picker").listeners.click();
  assert.equal(indigoButton.focused, true);

  nodes.get("theme-menu").listeners.keydown({
    key: "ArrowDown",
    preventDefault() {},
  });
  assert.equal(document.documentElement.dataset.theme, "obsidian");
  assert.equal(store.get("clauz3-theme"), "obsidian");
});

test("standalone profile.js validates stored theme and does not overwrite clauz3Theme", () => {
  const source = readFileSync(
    new URL("../../src/clauz3/approvals/web/profile.js", import.meta.url),
    "utf8",
  );
  function runProfile(storedValue, withClauz3Theme = false) {
    const view = new Element();
    view.hidden = true;
    view.querySelector = () => new Element();
    const doc = new Element();
    doc.documentElement = new Element();
    doc.documentElement.dataset.theme = "indigo";
    doc.getElementById = (id) => (id === "approvals-view" ? view : new Element());
    const context = vm.createContext({
      document: doc,
      window: {},
      localStorage: { getItem: () => storedValue },
      ...(withClauz3Theme ? { clauz3Theme: {} } : {}),
    });
    context.globalThis = context;
    vm.runInContext(source, context);
    return doc.documentElement.dataset.theme;
  }
  assert.equal(runProfile("obsidian", false), "obsidian");
  assert.equal(runProfile("invalid-theme", false), "parchment");
  assert.equal(runProfile("invalid-theme", true), "indigo");
});

// Run with `just node-test` (part of `just test` and `just ui-test`).
import assert from "node:assert/strict";
import { test } from "node:test";

import "../../src/clauz3/sessions/web/markdown.js";

const { parse, parseInline } = globalThis.clauz3Markdown;

test("list items keep indented fences and inline markup", () => {
  const source = [
    "Checking the history needs `stdlib:git`. Do one of these:",
    "",
    "- **Libraries view:** add `stdlib:git`, then start again.",
    "- **Terminal:** run the commands below.",
    "  ```",
    "  clauz3 install stdlib:filesystem",
    "  clauz3 install stdlib:git",
    "  ```",
    "",
    "Once it's installed, I can check it.",
  ].join("\n");
  assert.deepEqual(parse(source), [
    { type: "paragraph", text: "Checking the history needs `stdlib:git`. Do one of these:" },
    {
      type: "list",
      ordered: false,
      start: 1,
      items: [
        [{ type: "paragraph", text: "**Libraries view:** add `stdlib:git`, then start again." }],
        [
          { type: "paragraph", text: "**Terminal:** run the commands below." },
          {
            type: "code",
            lang: "",
            text: "clauz3 install stdlib:filesystem\nclauz3 install stdlib:git",
          },
        ],
      ],
    },
    { type: "paragraph", text: "Once it's installed, I can check it." },
  ]);
});

test("an unclosed fence runs to the end while streaming", () => {
  assert.deepEqual(parse("```sh\nfind . -name '*.py'"), [
    { type: "code", lang: "sh", text: "find . -name '*.py'" },
  ]);
});

test("headings, rules, quotes and ordered lists", () => {
  assert.deepEqual(parse("## Plan\n---\n> careful\n\n3. three\n4. four"), [
    { type: "heading", level: 2, text: "Plan" },
    { type: "rule" },
    { type: "quote", children: [{ type: "paragraph", text: "careful" }] },
    {
      type: "list",
      ordered: true,
      start: 3,
      items: [
        [{ type: "paragraph", text: "three" }],
        [{ type: "paragraph", text: "four" }],
      ],
    },
  ]);
});

test("nested lists and loose items stay in one list", () => {
  const [list] = parse("- a\n  - b\n\n- c");
  assert.equal(list.items.length, 2);
  assert.deepEqual(list.items[0][1], {
    type: "list",
    ordered: false,
    start: 1,
    items: [[{ type: "paragraph", text: "b" }]],
  });
});

test("tables with alignment", () => {
  assert.deepEqual(parse("| Tier | Runs |\n|:--|--:|\n| `ui` | fast |"), [
    {
      type: "table",
      align: ["left", "right"],
      header: ["Tier", "Runs"],
      rows: [["`ui`", "fast"]],
    },
  ]);
});

test("raw HTML stays literal text", () => {
  assert.deepEqual(parse("<script>alert(1)</script>"), [
    { type: "paragraph", text: "<script>alert(1)</script>" },
  ]);
  assert.deepEqual(parseInline("<b>x</b>"), [{ type: "text", text: "<b>x</b>" }]);
});

test("inline markup", () => {
  assert.deepEqual(parseInline("**bold** and *em* and `a*b*c` and ~~gone~~"), [
    { type: "strong", children: [{ type: "text", text: "bold" }] },
    { type: "text", text: " and " },
    { type: "em", children: [{ type: "text", text: "em" }] },
    { type: "text", text: " and " },
    { type: "code", text: "a*b*c" },
    { type: "text", text: " and " },
    { type: "del", children: [{ type: "text", text: "gone" }] },
  ]);
});

test("snake_case identifiers are not emphasis", () => {
  assert.deepEqual(parseInline("mcp__clauz3__tools and file_name_here"), [
    { type: "text", text: "mcp__clauz3__tools and file_name_here" },
  ]);
});

test("only safe link schemes become links", () => {
  assert.deepEqual(parseInline("[docs](https://example.org/a)"), [
    { type: "link", href: "https://example.org/a", children: [{ type: "text", text: "docs" }] },
  ]);
  for (const source of ["[x](javascript:alert(1))", "[x](data:text/html,hi)"]) {
    const nodes = parseInline(source);
    assert.ok(nodes.every((node) => node.type === "text"));
    assert.equal(nodes.map((node) => node.text).join(""), source);
  }
});

test("bare URLs link without trailing punctuation", () => {
  assert.deepEqual(parseInline("See https://example.org/run/1."), [
    { type: "text", text: "See " },
    {
      type: "link",
      href: "https://example.org/run/1",
      children: [{ type: "text", text: "https://example.org/run/1" }],
    },
    { type: "text", text: "." },
  ]);
});

// Follow the only child down a chain of quotes or single-item lists.
function nesting(source) {
  let [block] = parse(source);
  let depth = 0;
  while (block && (block.type === "quote" || block.type === "list")) {
    const children = block.type === "quote" ? block.children : block.items[0];
    assert.ok(children.length <= 1);
    [block] = children;
    depth++;
  }
  return { depth, block };
}

test("ordinary nesting is unchanged", () => {
  for (const marker of ["> ", "- "])
    assert.deepEqual(nesting(marker.repeat(8) + "deep"), {
      depth: 8,
      block: { type: "paragraph", text: "deep" },
    });
});

test("runaway nesting becomes plain text instead of overflowing the stack", () => {
  for (const marker of ["> ", "- "]) {
    const { depth, block } = nesting(marker.repeat(5000) + "x");
    // 33 containers: the outermost block plus 32 nested levels.
    assert.equal(depth, 33);
    assert.deepEqual(block, {
      type: "paragraph",
      text: marker.repeat(5000 - depth) + "x",
    });
  }
  // An empty block at the limit stays empty.
  assert.deepEqual(nesting("> ".repeat(33)), { depth: 33, block: undefined });
});

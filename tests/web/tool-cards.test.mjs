import assert from "node:assert/strict";
import { test } from "node:test";
import "../../src/clauz3/sessions/web/tool-cards.js";
const { model, catalogue } = globalThis.clauz3ToolCards;
const content = (value) => [
  {
    type: "content",
    content: { type: "text", text: JSON.stringify({ result: value }) },
  },
];
const call = (name, result, extra = {}) => ({
  title: `mcp__clauz3__${name}`,
  status: "completed",
  content: content(result),
  ...extra,
});

test("the six built-in tools have plain-language names", () => {
  for (const [key, title] of Object.entries({
    tools: "Find tools",
    libraries: "Browse libraries",
    list_skills: "Browse guides",
    read_skill: "Read guide",
    run: "Run program",
    status: "Check runs",
  })) {
    assert.equal(model(call(key, "")).title, title);
    assert.equal(model(call(key, ""), true).title, title);
  }
});
test("unrestricted tools retain their identity, including similarly named tools", () => {
  for (const title of [
    "mcp__other__run",
    "run",
    "tools",
    "shell",
    "not_mcp__clauz3__run",
  ]) {
    assert.equal(model({ title }, true).key, null);
    assert.equal(model({ title }, true).title, title);
  }
  assert.equal(model({ title: "tools" }).key, "tools");
});
test("Codex MCP arguments and JSON-encoded inputs are normalized", () => {
  const result = model(
    {
      title: "Call MCP tool",
      rawInput: {
        server: "clauz3",
        tool: "tools",
        arguments: { query: "read_lines" },
      },
      rawOutput: {
        content: [
          { type: "text", text: JSON.stringify({ result: "one\ntwo" }) },
        ],
      },
    },
    true,
  );
  assert.equal(result.title, "Find tools");
  assert.equal(result.detail, "read_lines");
  assert.equal(result.value, "one\ntwo");
  assert.equal(
    model(call("read_skill", "# Guide", { rawInput: '{"id":"filesystem"}' }))
      .detail,
    "filesystem",
  );
});
test("MCP lists, structured content, and plain strings remain readable", () => {
  const guides = [{ id: "fs", name: "Filesystem", preview: "Read files" }];
  assert.deepEqual(model(call("list_skills", guides)).value, guides);
  assert.deepEqual(
    model({ rawOutput: { structuredContent: { result: guides } } }).value,
    guides,
  );
  assert.deepEqual(model({ rawOutput: [] }).value, []);
  assert.equal(
    model({
      content: [
        { type: "content", content: { type: "text", text: "plain\ntext" } },
      ],
    }).value,
    "plain\ntext",
  );
  assert.equal(model({ rawOutput: "{broken json" }).value, "{broken json");
});
test("run output decodes real newlines and moves receipt and status into raw details", () => {
  const raw =
    "run id: 123\nphase: completed\nexit code: 0\nlines: 507\nmain: proved! guarantee\napproval: approved_once\nreceipt=clz2.signature\nexecuted: main\n\nStatus: " +
    JSON.stringify({ run_id: "123", phase: "completed", exit_code: 0 });
  const data = call("run", raw, {
    rawInput: { source: "def main():\n    print('hello')" },
  });
  const result = model(data);
  assert.equal(result.status, "Finished");
  assert.equal(result.run.exitCode, 0);
  assert.match(result.run.text, /lines: 507\nmain: proved!/);
  assert.match(result.run.text, /approval: approved_once/);
  assert.doesNotMatch(result.run.text, /receipt=|Status:/);
  assert.equal(data.content[0].content.text, JSON.stringify({ result: raw }));
  assert.equal(result.input.source, "def main():\n    print('hello')");
});
test("a completed tool call does not imply a successful program", () => {
  assert.equal(
    model(call("run", "run id: x\nphase: refused\nexit code: 2\nProof failed"))
      .status,
    "Did not run",
  );
  assert.equal(
    model(call("run", "exit code: 2\nNo libraries selected")).failed,
    true,
  );
  assert.equal(
    model(call("run", "unrecognized result")).status,
    "Tool finished",
  );
  const timeout = model(
    call(
      "run",
      'run id: x\nexit code: 2\nDeadline exceeded\nLast obligation: {"phase":"refused","reason":"Timeout"}',
    ),
  );
  assert.equal(timeout.status, "Did not run");
  assert.equal(timeout.run.reason, "Timeout");
  const pending = model(
    call("run", 'run id: x\nStatus: {"phase":"awaiting_approval"}'),
  );
  assert.equal(pending.status, "Awaiting approval");
  assert.equal(
    model(call("run", "phase: completed\nexit code: 0\n", { status: "failed" }))
      .status,
    "Failed",
  );
});
test("unknown results and malformed trailers are retained", () => {
  const result = model(
    call("run", "exit code: 1\nSomething failed\nStatus: {broken}"),
  );
  assert.match(result.run.text, /Status: \{broken\}/);
  assert.equal(model({ rawInput: false, rawOutput: 0 }).value, 0);
  assert.equal(model({}).value, null);
});
test("API docs group effects and contracts without executing or interpreting HTML", () => {
  const value =
    "Public API.\n\n# .clauz3/tools/filesystem/trusted/effects.py\n\nEffect: read_lines(path: str) -> list[str]\nImport: from tools.filesystem.trusted.effects import read_lines\nRead UTF-8 lines.\nPreconditions:\nrequires(lambda path: len(path) > 0)\n\n# .clauz3/tools/filesystem/trusted/contracts.py\n\nContract (use inside @clauz3.guarantee): read_only()\nImport: from tools.filesystem.trusted.contracts import read_only\n<script>danger()</script>";
  const parsed = catalogue(value);
  assert.equal(parsed.intro, "Public API.");
  assert.equal(parsed.sections.length, 2);
  assert.equal(
    parsed.sections[0].entries[0].signature,
    "read_lines(path: str) -> list[str]",
  );
  assert.equal(parsed.sections[1].entries[0].kind, "Contract");
  assert.ok(
    parsed.sections[1].entries[0].lines.includes("<script>danger()</script>"),
  );
});

test("MCP errors remain failures even when the adapter says completed", () => {
  assert.equal(
    model(
      call("tools", "", {
        rawOutput: {
          isError: true,
          content: [{ type: "text", text: "Unavailable" }],
        },
      }),
    ).status,
    "Failed",
  );
  assert.equal(
    model(
      call("read_skill", "", {
        content: [
          {
            type: "content",
            content: {
              type: "text",
              text: JSON.stringify({ isError: true, result: "Unknown guide" }),
            },
          },
        ],
      }),
    ).status,
    "Failed",
  );
});

test("ordinary output resembling a receipt is not removed", () => {
  assert.match(
    model(call("run", "exit code: 0\nreceipt=order-123")).run.text,
    /receipt=order-123/,
  );
});

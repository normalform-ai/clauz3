import assert from "node:assert/strict";
import { test } from "node:test";
import "../../src/clauz3/sessions/web/history.js";

const group = globalThis.clauz3History.group;
const now = new Date(2026, 9, 18, 15, 0);
const at = (days, hour = 12) => new Date(2026, 9, 18 - days, hour).getTime() / 1000;

test("conversations are grouped by calendar day, newest first", () => {
  const groups = group(
    [
      { id: "old", updated: at(90) },
      { id: "week", updated: at(3) },
      { id: "morning", updated: at(0, 9) },
      { id: "yesterday", updated: at(1, 23) },
      { id: "afternoon", updated: at(0, 14) },
      { id: "month", updated: at(12) },
    ],
    now,
  );
  assert.deepEqual(
    groups.map((g) => [g.label, g.conversations.map((c) => c.id)]),
    [
      ["Today", ["afternoon", "morning"]],
      ["Yesterday", ["yesterday"]],
      ["Previous 7 days", ["week"]],
      ["Previous 30 days", ["month"]],
      ["Older", ["old"]],
    ],
  );
});

test("empty groups are omitted", () => {
  assert.deepEqual(group([], now), []);
  assert.deepEqual(
    group([{ id: "a", updated: at(0) }], now).map((g) => g.label),
    ["Today"],
  );
});

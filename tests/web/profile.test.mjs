// Run with `just node-test` (part of `just test` and `just ui-test`).
import assert from "node:assert/strict";
import { test } from "node:test";

// profile.js looks up its view at load and stops when the page has none.
globalThis.document = { getElementById: () => null };
await import("../../src/clauz3/approvals/web/profile.js");

const { isApproved, decisionTitle, decisionBadge } =
  globalThis.clauz3ProfileDecisions;

test("only the exact approving decisions count as approved", () => {
  for (const decision of ["approved_once", "approved_remember", "auto_approved"])
    assert.equal(isApproved(decision), true, decision);
  for (const decision of [
    "rejected",
    "rejected_contract",
    "request_more",
    "auto_rejected",
    null,
    undefined,
    "",
  ])
    assert.equal(isApproved(decision), false, String(decision));
});

// Issue #62: profiles written before the vocabulary was closed may hold these.
for (const legacy of ["approved", "not_approved", "Approved_Once", "toString"]) {
  test(`a stored ${JSON.stringify(legacy)} is unknown, not approved`, () => {
    assert.equal(isApproved(legacy), false);
    assert.equal(decisionBadge(legacy), "Unknown");
    assert.equal(
      decisionTitle(legacy),
      `Unknown decision “${legacy}” (not approved)`,
    );
  });
}

test("known decisions keep their labels and badges", () => {
  assert.equal(decisionTitle("approved_once"), "Approved");
  assert.equal(decisionBadge("approved_once"), "Approved");
  assert.equal(decisionTitle("rejected_contract"), "Contract rejected");
  assert.equal(decisionBadge("rejected_contract"), "Reviewed");
  assert.equal(decisionTitle(null), "Awaiting decision");
  assert.equal(decisionBadge(null), "Pending");
});

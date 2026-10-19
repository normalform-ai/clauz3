import assert from "node:assert/strict";
import { test } from "node:test";
import "../../src/clauz3/sessions/web/run-status.js";

const status = globalThis.clauz3RunStatus;
const lastProof = {
  description: "guarantee",
  predicate: "fs.reads_at_most(2)",
  result: "guarantee",
  conclusion: "proved!",
};

test("completed actions separate lifecycle from stale proof progress", () => {
  const result = status({ ...lastProof, phase: "completed", exit_code: 0 });
  assert.match(result.summary, /completed successfully/);
  assert.doesNotMatch(result.summary, /reads_at_most|Result:|Exit code/);
  assert.match(result.diagnostics, /Expression: fs.reads_at_most\(2\)/);
  assert.match(result.diagnostics, /Proof conclusion: proved!/);
  assert.match(result.diagnostics, /Exit code: 0/);
});

test("approval and execution are separate states", () => {
  assert.match(status({ phase: "awaiting_approval" }).summary, /Waiting for your decision/);
  assert.doesNotMatch(status({ phase: "awaiting_approval" }).summary, /completed|Running/);
  assert.match(status({ phase: "executing" }).summary, /Running the approved action/);
});

test("failure or cancellation cannot inherit a successful proof as action success", () => {
  for (const phase of ["refused", "failed", "cancelled", "unknown"]) {
    const result = status({ ...lastProof, phase, reason: "Important failure", exit_code: 2 });
    assert.doesNotMatch(result.summary, /Checks passed|completed successfully/);
    assert.equal(result.reason, "Important failure");
    assert.match(result.diagnostics, /Exit code: 2/);
  }
});

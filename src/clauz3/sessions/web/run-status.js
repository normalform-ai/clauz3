/* Separate the run lifecycle from the last individual proof obligation. */
globalThis.clauz3RunStatus = (run) => {
  const messages = {
    checking: "Checking the program before requesting approval.",
    proving: "Checking every declared guarantee.",
    submitting_approval: "Checks passed. Preparing the approval request.",
    awaiting_approval: "Checks passed. Waiting for your decision.",
    executing: "Checks passed. Running the approved action.",
    completed: "Checks passed. The approved action completed successfully.",
    refused: "The action did not run.",
    failed: "The action failed. Review the diagnostics below.",
    cancelled: "The run was stopped. Review diagnostics for execution details.",
  };
  return {
    summary: messages[run.phase] || "Run status unavailable.",
    reason: run.reason || "",
    diagnostics: [
      run.description ? `Last check: ${run.description}` : "",
      run.predicate ? `Expression: ${run.predicate}` : "",
      run.conclusion ? `Proof conclusion: ${run.conclusion}` : "",
      run.result ? `Internal result: ${run.result}` : "",
      run.exit_code !== undefined ? `Exit code: ${run.exit_code}` : "",
    ].filter(Boolean).join("\n"),
  };
};

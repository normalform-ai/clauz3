/* Shared profile component. Decisions still use the existing authority APIs. */
(() => {
  // The closed decision vocabulary of clauz3.approvals.decisions.Decision
  // (tests/test_approval_profiles.py keeps these lists in step with it).
  // Matching is exact: profiles written before issue #62 may hold other
  // strings such as "approved", which are unknown and never approvals.
  const APPROVED = ["approved_once", "approved_remember", "auto_approved"];
  const AUTOMATIC = ["auto_approved", "auto_rejected"];
  const REMEMBERABLE = [
    "approved_once",
    "approved_remember",
    "rejected",
    "rejected_contract",
  ];
  const labels = {
    approved_once: "Approved",
    approved_remember: "Approved and remembered",
    rejected: "Rejected",
    rejected_contract: "Contract rejected",
    request_more: "More information requested",
    auto_approved: "Automatically approved",
    auto_rejected: "Automatically rejected",
  };
  const known = (decision) => Object.hasOwn(labels, decision);
  const isApproved = (decision) => APPROVED.includes(decision);
  const decisionTitle = (decision) =>
    !decision
      ? "Awaiting decision"
      : known(decision)
        ? labels[decision]
        : `Unknown decision “${decision}” (not approved)`;
  const decisionBadge = (decision) =>
    isApproved(decision)
      ? "Approved"
      : !decision
        ? "Pending"
        : known(decision)
          ? "Reviewed"
          : "Unknown";
  globalThis.clauz3ProfileDecisions = {
    APPROVED,
    AUTOMATIC,
    REMEMBERABLE,
    labels,
    isApproved,
    decisionTitle,
    decisionBadge,
  };

  const $ = (id) => document.getElementById(id);
  const view = $("approvals-view");
  if (!view) return;
  let snapshot = { rules: [], history: [] },
    tab = "rules",
    loading = false,
    saving = false;
  const text = (tag, value, cls) => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (cls) node.className = cls;
    return node;
  };
  function details(event) {
    const record = event.record || {};
    const container = text("div", "", "profile-detail");
    container.append(window.clauz3RenderGuarantees(record));
    const disclosure = text("details", "");
    disclosure.append(text("summary", "Program and decision details"));
    if (record.program) disclosure.append(text("pre", record.program));
    disclosure.append(text("pre", JSON.stringify(event, null, 2)));
    container.append(disclosure);
    return container;
  }
  function action(label, method, id) {
    const button = text("button", label);
    button.disabled = saving;
    button.addEventListener("click", async () => {
      if (saving) return;
      saving = true;
      view
        .querySelectorAll(".profile-card button")
        .forEach((b) => (b.disabled = true));
      $("profile-result").textContent = "Saving…";
      try {
        const response = await fetch(`/api/profile/${method}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id }),
        });
        if (!response.ok)
          throw new Error(
            `Could not save the rule (${response.status}). Please try again.`,
          );
        await load();
        $("profile-result").textContent =
          method === "disable"
            ? "Rule disabled. Future requests will be reviewed again."
            : "Decision remembered for matching requests.";
      } catch (error) {
        $("profile-result").textContent = error.message;
      } finally {
        saving = false;
        $("profile-refresh").disabled = false;
        render();
      }
    });
    return button;
  }
  function render() {
    const { rules, history } = snapshot;
    $("rules-count").textContent = rules.filter((r) => r.enabled).length;
    $("decisions-count").textContent = history.filter((e) => e.decision).length;
    $("automatic-count").textContent = history.filter((e) =>
      AUTOMATIC.includes(e.decision),
    ).length;
    const container = $("profile-cards");
    container.replaceChildren();
    const query = $("profile-search").value.trim().toLowerCase();
    const entries = tab === "rules" ? rules : history;
    for (const entry of entries) {
      const event =
        tab === "rules"
          ? history.find((e) => e.id === entry.history_id)
          : entry;
      if (!event) continue;
      if (
        query &&
        !JSON.stringify([entry, event, labels[event.decision]])
          .toLowerCase()
          .includes(query)
      )
        continue;
      const card = text("article", "", "profile-card");
      const heading = text("div", "", "profile-card-heading");
      const allowed =
        tab === "rules"
          ? entry.decision === "allow"
          : isApproved(entry.decision);
      const title =
        tab === "rules"
          ? `${allowed ? "Approve" : "Reject"} matching contracts`
          : decisionTitle(entry.decision);
      heading.append(
        text("h3", title),
        text(
          "span",
          tab === "rules"
            ? entry.enabled
              ? "Enabled"
              : "Disabled"
            : decisionBadge(entry.decision),
          `profile-badge ${tab === "rules" && !entry.enabled ? "disabled" : allowed ? "allowed" : "reviewed"}`,
        ),
      );
      card.append(
        heading,
        text(
          "p",
          new Date(event.created * 1000).toLocaleString(),
          "profile-date",
        ),
        details(event),
      );
      if (tab === "rules" && entry.enabled)
        card.append(action("Disable rule", "disable", entry.id));
      if (tab === "history") {
        const rule = rules.find((r) => r.history_id === event.id);
        const eligible =
          event.signature && REMEMBERABLE.includes(event.decision);
        if (rule?.enabled)
          card.append(action("Exclude from profile", "disable", rule.id));
        else if (eligible)
          card.append(action("Include in profile", "remember", event.id));
      }
      container.append(card);
    }
    if (!container.children.length) {
      const empty = text("div", "", "profile-empty");
      empty.append(
        text("div", "✓", "empty-mark"),
        text(
          "h2",
          query
            ? "No matching decisions"
            : tab === "rules"
              ? "Every decision starts with you."
              : "Your decisions will appear here.",
        ),
        text(
          "p",
          query
            ? "Try another contract or keyword."
            : tab === "rules"
              ? "Remember a reviewed approval or rejection to handle matching requests automatically."
              : "Review an action in your conversation to start building your history.",
        ),
      );
      container.append(empty);
    }
  }
  async function load() {
    if (loading) return;
    loading = true;
    $("profile-refresh").disabled = true;
    view
      .querySelectorAll(".profile-card button")
      .forEach((b) => (b.disabled = true));
    $("profile-result").textContent = "Loading approvals…";
    try {
      const response = await fetch("/api/profile", { cache: "no-store" });
      if (response.status === 404) {
        snapshot = { rules: [], history: [] };
        render();
        $("profile-result").textContent =
          "Remembered profiles are unavailable for this launch. Your actions still use the configured approval policy.";
        return;
      }
      if (!response.ok)
        throw new Error(
          "Could not load approvals. Reopen your private login link if the session expired.",
        );
      snapshot = await response.json();
      render();
      $("profile-result").textContent = "";
    } catch (error) {
      $("profile-result").textContent = error.message;
    } finally {
      loading = false;
      $("profile-refresh").disabled = saving;
      view
        .querySelectorAll(".profile-card button")
        .forEach((b) => (b.disabled = saving));
    }
  }
  function select(next) {
    tab = next;
    for (const [id, selected] of [
      ["rules-tab", next === "rules"],
      ["decisions-tab", next === "history"],
    ]) {
      $(id).setAttribute("aria-selected", selected);
      $(id).tabIndex = selected ? 0 : -1;
    }
    $("profile-cards").setAttribute(
      "aria-labelledby",
      next === "rules" ? "rules-tab" : "decisions-tab",
    );
    render();
  }
  $("rules-tab").addEventListener("click", () => select("rules"));
  $("decisions-tab").addEventListener("click", () => select("history"));
  view
    .querySelector('[role="tablist"]')
    .addEventListener("keydown", (event) => {
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        select(
          event.key === "Home"
            ? "rules"
            : event.key === "End"
              ? "history"
              : tab === "rules"
                ? "history"
                : "rules",
        );
        $(tab === "rules" ? "rules-tab" : "decisions-tab").focus();
      }
    });
  $("profile-search").addEventListener("input", render);
  $("profile-refresh").addEventListener("click", load);
  document.addEventListener("clauz3:approvals", load);
  if (!view.hidden) load();
})();

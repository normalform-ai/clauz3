/* Presentation only: tool text never grants approval or executes code.
   Keep the original ACP payload available alongside every readable view. */
(() => {
  const titles = {
    tools: "Find tools",
    libraries: "Browse libraries",
    list_skills: "Browse guides",
    read_skill: "Read guide",
    run: "Run program",
    status: "Check runs",
  };
  const phases = {
    checking: "Checking program",
    proving: "Proving guarantees",
    submitting_approval: "Requesting approval",
    awaiting_approval: "Awaiting approval",
    executing: "Running",
    completed: "Finished",
    refused: "Did not run",
    failed: "Failed",
    cancelled: "Stopped",
  };
  const object = (value) =>
    value !== null && typeof value === "object" && !Array.isArray(value);
  const pretty = (value) =>
    typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "";
  function json(value) {
    if (typeof value !== "string") return value;
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }
  function identify(data, unrestricted) {
    const input = json(data.rawInput);
    if (input?.server === "clauz3" && Object.hasOwn(titles, input.tool))
      return input.tool;
    for (const name of [data.name, data.title]) {
      if (typeof name !== "string") continue;
      const match = name.match(/^(?:mcp__clauz3__|clauz3[./_])([a-z_]+)$/);
      const key = match?.[1] || (!unrestricted ? name : "");
      if (Object.hasOwn(titles, key)) return key;
    }
    return null;
  }
  // ACP embeds MCP text; MCP may in turn wrap the value in {result: ...}.
  // Limit unwrapping and keep unfamiliar shapes intact for the fallback view.
  function unwrap(value, depth = 0) {
    if (depth > 6) return value;
    const parsed = json(value);
    if (parsed !== value) return unwrap(parsed, depth + 1);
    if (!object(value)) return value;
    if (Object.hasOwn(value, "result")) return unwrap(value.result, depth + 1);
    if (value.structuredContent != null)
      return unwrap(value.structuredContent, depth + 1);
    if (Array.isArray(value.content)) {
      const texts = value.content
        .map((part) => part?.text ?? part?.content?.text)
        .filter((text) => typeof text === "string");
      if (texts.length === 1) return unwrap(texts[0], depth + 1);
      if (texts.length)
        return texts
          .map((text) => pretty(unwrap(text, depth + 1)))
          .join("\n\n");
    }
    return value;
  }
  function output(data) {
    if (data.rawOutput != null) return unwrap(data.rawOutput);
    if (data.content?.length) return unwrap({ content: data.content });
    return null;
  }
  function resultFailed(value, depth = 0) {
    if (depth > 6) return false;
    value = json(value);
    if (!object(value)) return false;
    if (value.isError === true) return true;
    if (Object.hasOwn(value, "result") && resultFailed(value.result, depth + 1))
      return true;
    return (
      Array.isArray(value.content) &&
      value.content.some((part) =>
        resultFailed(part?.text ?? part?.content?.text, depth + 1),
      )
    );
  }
  function runResult(value) {
    if (typeof value !== "string") return null;
    let text = value,
      record = null;
    const trailer = text.match(
      /\n(?:Status|Last obligation): (\{[^\n]*\})\s*$/,
    );
    if (trailer && object(json(trailer[1]))) {
      record = json(trailer[1]);
      text = text.slice(0, trailer.index);
    }
    // Only remove the bridge's leading fields; a program may print similar text.
    const header = text.match(
      /^(?:run id: ([^\n]+)\n)?(?:phase: ([^\n]+)\n)?exit code: (-?\d+)\n?/,
    );
    if (!record && !header) return null;
    if (header) text = text.slice(header[0].length);
    return {
      phase: record?.phase || header?.[2] || "",
      exitCode: record?.exit_code ?? (header ? Number(header[3]) : null),
      reason: record?.reason || "",
      // Signed receipts remain in Raw tool data; they overwhelm the readable log.
      text: text.replace(/^receipt=clz2\.[^\n]*(?:\n|$)/gm, "").trim(),
    };
  }
  function catalogue(text) {
    const sections = [];
    let current = { path: "", entries: [] },
      entry = null;
    const intro = [];
    for (const line of text.split("\n")) {
      if (/^# .+\.py$/.test(line)) {
        current = { path: line.slice(2), entries: [] };
        sections.push(current);
        entry = null;
      } else if (
        /^(Effect|Contract \(use inside @clauz3\.guarantee\)|Row type): /.test(
          line,
        )
      ) {
        if (!sections.includes(current)) sections.push(current);
        const split = line.indexOf(": ");
        entry = {
          kind: line.slice(0, split).startsWith("Contract")
            ? "Contract"
            : line.slice(0, split),
          signature: line.slice(split + 2),
          lines: [],
        };
        current.entries.push(entry);
      } else if (entry) entry.lines.push(line);
      else intro.push(line);
    }
    return { intro: intro.join("\n").trim(), sections };
  }
  function model(data, unrestricted = false) {
    const key = identify(data, unrestricted);
    let input = json(data.rawInput);
    if (input?.server === "clauz3")
      input = json(input.arguments ?? input.input ?? {});
    const value = output(data);
    const run = key === "run" ? runResult(value) : null;
    let status =
      {
        pending: "Waiting",
        in_progress: "Working…",
        completed: "Done",
        failed: "Failed",
      }[data.status] || "Waiting";
    if (key === "run") {
      status = data.status === "completed" ? "Tool finished" : status;
      if (run && phases[run.phase]) status = phases[run.phase];
      else if (run?.exitCode != null && run.exitCode !== 0)
        status = "Did not finish";
    }
    // A transport failure always remains visible even if partial output says done.
    if (
      data.status === "failed" ||
      resultFailed(data.rawOutput) ||
      resultFailed({ content: data.content })
    )
      status = "Failed";
    return {
      key,
      input,
      value,
      run,
      status,
      title: key ? titles[key] : data.title || data.name || "Tool call",
      detail:
        key === "read_skill"
          ? input?.id
          : ["tools", "libraries"].includes(key)
            ? input?.query
            : "",
      failed: ["Failed", "Did not run", "Did not finish"].includes(status),
    };
  }

  const node = (tag, value = "", cls = "") => {
    const element = document.createElement(tag);
    element.textContent = value;
    if (tag === "pre") element.tabIndex = 0;
    if (cls) element.className = cls;
    return element;
  };
  function section(parent, title, content, cls = "") {
    const block = node("section", "", `tool-section ${cls}`);
    const heading = node("div", "", "tool-section-heading");
    heading.append(node("h4", title));
    if (content.tagName === "PRE")
      heading.append(
        clauz3Copy.button(
          `Copy ${title.toLowerCase()}`,
          () => content.textContent,
        ),
      );
    block.append(heading, content);
    parent.append(block);
  }
  function code(value, label = "Copy code") {
    const pre = node("pre", "", "tool-code");
    pre.append(node("code", value));
    return clauz3Copy.block(pre, label);
  }
  function markdown(value) {
    const body = node("div", "", "markdown tool-document");
    body.tabIndex = 0;
    body.append(globalThis.clauz3Markdown.render(value));
    return body;
  }
  function renderCatalogue(parent, value, query) {
    const parsed = catalogue(value);
    if (!parsed.sections.length)
      return section(parent, "Results", node("p", value, "tool-prose"));
    if (parsed.intro) parent.append(node("p", parsed.intro, "tool-note"));
    for (const group of parsed.sections) {
      const list = node("div", "", "tool-catalogue");
      for (const entry of group.entries) {
        const item = node("details", "", "tool-api");
        item.open = Boolean(
          query && entry.signature.toLowerCase().includes(query.toLowerCase()),
        );
        const summary = node("summary");
        summary.append(
          node("span", entry.kind, "tool-kind"),
          node("code", entry.signature),
        );
        const content = entry.lines.join("\n").trim();
        const [description, conditions] = content.split(/\nPreconditions:\n/);
        const importLine = description.match(/^Import: (.+)\n?/);
        if (importLine)
          item.append(
            summary,
            code(importLine[1]),
            node("p", description.slice(importLine[0].length), "tool-prose"),
          );
        else item.append(summary, node("p", description, "tool-prose"));
        if (conditions) section(item, "Preconditions", code(conditions));
        list.append(item);
      }
      section(parent, group.path || "Available tools", list);
    }
  }
  function renderLibraries(parent, value) {
    const blocks = value.split(/\n(?=\S)/);
    const list = node("ul", "", "tool-list");
    for (const block of blocks) {
      const [title, ...lines] = block.split("\n");
      const item = node("li");
      item.append(
        node("strong", title),
        node("p", lines.map((line) => line.trim()).join("\n"), "tool-prose"),
      );
      list.append(item);
    }
    section(parent, "Libraries", list);
  }
  function renderRecords(parent, records, skills) {
    const list = node("ul", "", "tool-list");
    for (const record of records) {
      const item = node("li");
      if (skills) {
        item.append(node("strong", record.name || record.id || "Guide"));
        if (record.id) item.append(node("code", record.id, "tool-guide-id"));
        if (record.preview)
          item.append(node("p", record.preview, "tool-prose"));
      } else {
        item.append(
          node("strong", phases[record.phase] || record.phase || "Run"),
        );
        if (record.run_id)
          item.append(node("code", record.run_id, "tool-guide-id"));
        for (const key of ["reason", "description", "result"])
          if (record[key])
            item.append(node("p", pretty(record[key]), "tool-prose"));
        const facts = [];
        if (record.exit_code != null)
          facts.push(`Exit code ${record.exit_code}`);
        if (record.elapsed_seconds != null)
          facts.push(`${record.elapsed_seconds}s elapsed`);
        if (facts.length)
          item.append(node("p", facts.join(" · "), "tool-note"));
      }
      list.append(item);
    }
    section(
      parent,
      skills ? "Available guides" : "Runs",
      records.length
        ? list
        : node(
            "p",
            skills ? "No guides available." : "No runs yet.",
            "tool-note",
          ),
    );
  }
  function render(item, data, unrestricted = false) {
    const view = model(data, unrestricted);
    item.summary.replaceChildren(node("span", view.title, "tool-title"));
    if (typeof view.detail === "string" && view.detail)
      item.summary.append(node("span", view.detail, "tool-context"));
    item.summary.append(
      node("span", view.status, `tool-status${view.failed ? " failed" : ""}`),
    );
    // Preserve nested disclosures during streamed updates.
    const disclosures = new Map(
      [...item.body.querySelectorAll("details")].map((d) => [
        d.querySelector("summary")?.textContent,
        d.open,
      ]),
    );
    item.body.replaceChildren();
    const { key, input, value, run } = view;
    if (key === "run") {
      if (run) {
        const facts = node(
          "p",
          `${view.status}${run.exitCode != null ? ` · Exit code ${run.exitCode}` : ""}`,
          "tool-result",
        );
        item.body.append(facts);
        if (run.reason) item.body.append(node("p", run.reason, "tool-prose"));
        if (run.text)
          section(
            item.body,
            "Output & diagnostics",
            node("pre", run.text, "tool-output"),
          );
      } else if (value != null)
        section(item.body, "Result", node("pre", pretty(value), "tool-output"));
      if (typeof input?.source === "string")
        section(item.body, "Python program", code(input.source));
    } else if (key === "tools" && typeof value === "string") {
      if (input?.view === "source")
        section(item.body, "Implementation source", code(value));
      else
        renderCatalogue(
          item.body,
          value,
          typeof input?.query === "string" ? input.query : "",
        );
    } else if (key === "read_skill" && typeof value === "string")
      section(item.body, "Guide", markdown(value));
    else if (key === "libraries" && typeof value === "string")
      renderLibraries(item.body, value);
    else if (
      ["list_skills", "status"].includes(key) &&
      Array.isArray(value) &&
      value.every(object)
    )
      renderRecords(item.body, value, key === "list_skills");
    else {
      if (!key && input != null)
        section(item.body, "Input", code(pretty(input)));
      if (value != null)
        section(item.body, "Result", node("pre", pretty(value), "tool-output"));
    }
    if (value == null)
      item.body.append(
        node(
          "p",
          view.failed
            ? "The tool failed without reporting a result."
            : data.status === "completed"
              ? "No result reported."
              : "Waiting for the tool’s response…",
          "tool-note",
        ),
      );
    const raw = node("details", "", "tool-raw");
    if (value != null)
      item.body.append(
        clauz3Copy.button(
          "Copy full tool result",
          () => pretty(value),
          "Copy result",
        ),
      );
    raw.append(
      node("summary", "Raw tool data"),
      code(JSON.stringify(data, null, 2), "Copy raw tool data"),
    );
    item.body.append(raw);
    for (const detail of item.body.querySelectorAll("details")) {
      const title = detail.querySelector("summary")?.textContent;
      if (disclosures.has(title)) detail.open = disclosures.get(title);
    }
  }
  globalThis.clauz3ToolCards = { model, catalogue, render };
})();

/* Render agent Markdown as DOM nodes, never as HTML. Raw HTML in the source
   stays literal text, and only http(s) and mailto links become anchors.

   parse() and parseInline() are pure so they can be tested without a DOM;
   render() turns their output into a DocumentFragment. A message that is
   still streaming parses fine: an unclosed fence runs to the end. */
(() => {
  const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([^`\s]*)/;
  const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
  const RULE = /^ {0,3}([-*_])(\s*\1){2,}\s*$/;
  const QUOTE = /^ {0,3}> ?/;
  const ITEM = /^( *)([-*+]|\d{1,9}[.)])\s+/;
  const DELIMITER = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
  const blank = (line) => !line.trim();
  const indent = (line) => line.match(/^ */)[0].length;
  const startsBlock = (line) =>
    FENCE.test(line) ||
    HEADING.test(line) ||
    RULE.test(line) ||
    QUOTE.test(line) ||
    ITEM.test(line);

  function cells(line) {
    return line
      .trim()
      .replace(/^\|/, "")
      .replace(/(?<!\\)\|$/, "")
      .split(/(?<!\\)\|/)
      .map((cell) => cell.trim().replaceAll("\\|", "|"));
  }

  function parseLines(lines) {
    const blocks = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (blank(line)) {
        i++;
        continue;
      }
      const fence = line.match(FENCE);
      if (fence) {
        const [marker, lang] = [fence[1], fence[2]];
        const strip = indent(line);
        const body = [];
        i++;
        while (i < lines.length) {
          const close = lines[i].match(FENCE);
          if (
            close &&
            close[1][0] === marker[0] &&
            close[1].length >= marker.length &&
            !close[2]
          )
            break;
          body.push(lines[i].slice(Math.min(strip, indent(lines[i]))));
          i++;
        }
        i++;
        blocks.push({ type: "code", lang, text: body.join("\n") });
        continue;
      }
      const heading = line.match(HEADING);
      if (heading) {
        blocks.push({
          type: "heading",
          level: heading[1].length,
          text: heading[2],
        });
        i++;
        continue;
      }
      if (RULE.test(line)) {
        blocks.push({ type: "rule" });
        i++;
        continue;
      }
      if (QUOTE.test(line)) {
        const body = [];
        while (i < lines.length && QUOTE.test(lines[i]))
          body.push(lines[i++].replace(QUOTE, ""));
        blocks.push({ type: "quote", children: parseLines(body) });
        continue;
      }
      const item = line.match(ITEM);
      if (item) {
        const ordered = /\d/.test(item[2]);
        const list = {
          type: "list",
          ordered,
          start: ordered ? parseInt(item[2], 10) : 1,
          items: [],
        };
        const base = item[1].length;
        while (i < lines.length) {
          const next = lines[i].match(ITEM);
          if (
            !next ||
            next[1].length !== base ||
            /\d/.test(next[2]) !== ordered
          )
            break;
          const offset = Math.min(next[0].length, base + 2);
          const body = [lines[i].slice(next[0].length)];
          i++;
          while (i < lines.length) {
            const current = lines[i];
            if (blank(current)) {
              const following = lines.slice(i).find((l) => !blank(l));
              if (following === undefined || indent(following) < offset) break;
              body.push("");
            } else if (indent(current) >= offset) {
              body.push(current.slice(offset));
            } else if (!startsBlock(current) && !blank(body.at(-1))) {
              body.push(current.trim());
            } else break;
            i++;
          }
          list.items.push(parseLines(body));
          while (i < lines.length && blank(lines[i])) {
            const following = lines.slice(i).find((l) => !blank(l));
            const sibling = following?.match(ITEM);
            if (!sibling || sibling[1].length !== base) break;
            i++;
          }
        }
        blocks.push(list);
        continue;
      }
      if (line.includes("|") && DELIMITER.test(lines[i + 1] || "")) {
        const align = cells(lines[i + 1]).map((cell) =>
          cell.endsWith(":")
            ? cell.startsWith(":")
              ? "center"
              : "right"
            : cell.startsWith(":")
              ? "left"
              : "",
        );
        const header = cells(line);
        const rows = [];
        i += 2;
        while (i < lines.length && !blank(lines[i]) && lines[i].includes("|"))
          rows.push(cells(lines[i++]));
        blocks.push({ type: "table", align, header, rows });
        continue;
      }
      const body = [line.trim()];
      i++;
      while (i < lines.length && !blank(lines[i]) && !startsBlock(lines[i]))
        body.push(lines[i++].trim());
      blocks.push({ type: "paragraph", text: body.join("\n") });
    }
    return blocks;
  }

  /** Split Markdown source into a tree of blocks with raw inline text. */
  function parse(source) {
    return parseLines(source.replace(/\r\n?/g, "\n").split("\n"));
  }

  // Earliest match wins; on a tie the first pattern listed wins.
  const INLINE = [
    ["code", /(`+)([\s\S]*?[^`])\1(?!`)/],
    ["link", /\[([^\]\n]+)\]\(([^)\s]+)\)/],
    ["url", /https?:\/\/[^\s<>()]*[^\s<>().,;:!?'"*_~`]/],
    ["strong", /\*\*(?=\S)([\s\S]*?\S)\*\*|(?<!\w)__(?=\S)([\s\S]*?\S)__(?!\w)/],
    ["del", /~~(?=\S)([\s\S]*?\S)~~/],
    ["em", /\*(?=[^\s*])([\s\S]*?[^\s*])\*|(?<!\w)_(?=\S)([\s\S]*?\S)_(?!\w)/],
  ];
  const SAFE_URL = /^(https?:|mailto:)/i;

  /** Split inline text into text, code, link, strong, em and del nodes. */
  function parseInline(source) {
    const nodes = [];
    let rest = source;
    while (rest) {
      let best = null;
      for (const [type, pattern] of INLINE) {
        const match = pattern.exec(rest);
        if (match && (!best || match.index < best.match.index))
          best = { type, match };
      }
      if (!best) {
        nodes.push({ type: "text", text: rest });
        break;
      }
      const { type, match } = best;
      if (match.index) nodes.push({ type: "text", text: rest.slice(0, match.index) });
      rest = rest.slice(match.index + match[0].length);
      if (type === "code") {
        const body = match[2];
        const padded = /^ .*[^ ].* $/.test(body);
        nodes.push({ type, text: padded ? body.slice(1, -1) : body });
      } else if (type === "url") {
        nodes.push({ type: "link", href: match[0], children: [{ type: "text", text: match[0] }] });
      } else if (type === "link") {
        nodes.push(
          SAFE_URL.test(match[2])
            ? { type, href: match[2], children: parseInline(match[1]) }
            : { type: "text", text: match[0] },
        );
      } else {
        nodes.push({ type, children: parseInline(match[1] ?? match[2]) });
      }
    }
    return nodes;
  }

  const TAGS = { strong: "strong", em: "em", del: "del" };

  function renderInline(parent, nodes) {
    for (const node of nodes) {
      if (node.type === "text") {
        parent.append(node.text);
      } else if (node.type === "code") {
        const code = document.createElement("code");
        code.textContent = node.text;
        parent.append(code);
      } else if (node.type === "link") {
        const anchor = document.createElement("a");
        anchor.href = node.href;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        renderInline(anchor, node.children);
        parent.append(anchor);
      } else {
        const element = document.createElement(TAGS[node.type]);
        renderInline(element, node.children);
        parent.append(element);
      }
    }
  }

  const inline = (parent, source) => renderInline(parent, parseInline(source));

  function renderBlocks(parent, blocks) {
    for (const block of blocks) {
      let element;
      if (block.type === "paragraph") {
        element = document.createElement("p");
        inline(element, block.text);
      } else if (block.type === "heading") {
        element = document.createElement(`h${block.level}`);
        inline(element, block.text);
      } else if (block.type === "code") {
        element = document.createElement("pre");
        const code = document.createElement("code");
        code.textContent = block.text;
        if (block.lang) code.dataset.lang = block.lang;
        element.append(code);
        element = clauz3Copy.block(element, "Copy code");
      } else if (block.type === "rule") {
        element = document.createElement("hr");
      } else if (block.type === "quote") {
        element = document.createElement("blockquote");
        renderBlocks(element, block.children);
      } else if (block.type === "list") {
        element = document.createElement(block.ordered ? "ol" : "ul");
        if (block.ordered && block.start !== 1) element.start = block.start;
        for (const item of block.items) {
          const li = document.createElement("li");
          renderBlocks(li, item);
          element.append(li);
        }
      } else if (block.type === "table") {
        element = document.createElement("div");
        element.className = "table";
        const table = document.createElement("table");
        const row = (values, tag) => {
          const tr = document.createElement("tr");
          values.forEach((value, column) => {
            const cell = document.createElement(tag);
            if (block.align[column]) cell.style.textAlign = block.align[column];
            inline(cell, value);
            tr.append(cell);
          });
          return tr;
        };
        const head = document.createElement("thead");
        head.append(row(block.header, "th"));
        const body = document.createElement("tbody");
        for (const values of block.rows) body.append(row(values, "td"));
        table.append(head, body);
        element.append(table);
      }
      parent.append(element);
    }
  }

  /** Render Markdown source as a DocumentFragment of inert DOM nodes. */
  function render(source) {
    const fragment = document.createDocumentFragment();
    renderBlocks(fragment, parse(source));
    return fragment;
  }

  globalThis.clauz3Markdown = { parse, parseInline, render };
})();

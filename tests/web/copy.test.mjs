import assert from "node:assert/strict";
import { test } from "node:test";
import "../../src/clauz3/sessions/web/copy.js";
const { write } = globalThis.clauz3Copy;
function globals(t, values) {
  for (const [name, value] of Object.entries(values)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { value, configurable: true });
    t.after(() =>
      previous
        ? Object.defineProperty(globalThis, name, previous)
        : delete globalThis[name],
    );
  }
}
class Item {
  constructor(values) {
    this.values = values;
  }
}

test("plain text copy preserves Markdown and code exactly", async (t) => {
  let copied;
  globals(t, {
    ClipboardItem: Item,
    navigator: {
      clipboard: {
        write: async ([item]) => {
          copied = await (await item.values["text/plain"]).text();
        },
      },
    },
  });
  const source =
    '**Hello**\n\n```python\nprint("<script>literal</script>")\n```';
  await write(source);
  assert.equal(copied, source);
});
test("async handoffs start clipboard access before the request resolves", async (t) => {
  let resolve,
    started = false,
    copied;
  const handoff = new Promise((done) => {
    resolve = done;
  });
  globals(t, {
    ClipboardItem: Item,
    navigator: {
      clipboard: {
        write: async ([item]) => {
          started = true;
          copied = await (await item.values["text/plain"]).text();
        },
      },
    },
  });
  const operation = write(handoff);
  assert.equal(started, true);
  resolve("Conversation: uuid\nLocal transcript snapshot: /tmp/chat.json");
  await operation;
  assert.match(copied, /\/tmp\/chat.json/);
});
test("writeText is supported when ClipboardItem is unavailable", async (t) => {
  let copied;
  globals(t, {
    ClipboardItem: undefined,
    navigator: {
      clipboard: {
        writeText: async (text) => {
          copied = text;
        },
      },
    },
  });
  await write("response\nnext line");
  assert.equal(copied, "response\nnext line");
});
test("failed snapshot generation cannot be reported as a successful copy", async (t) => {
  globals(t, {
    ClipboardItem: Item,
    navigator: {
      clipboard: {
        write: async ([item]) => {
          await item.values["text/plain"];
        },
      },
    },
  });
  await assert.rejects(
    write(Promise.reject(new Error("Snapshot unavailable"))),
    /Snapshot unavailable/,
  );
});
test("clipboard denial and a later request failure are both handled", async (t) => {
  let reject;
  const pending = new Promise((_, fail) => {
    reject = fail;
  });
  globals(t, {
    ClipboardItem: Item,
    navigator: {
      clipboard: {
        write: async () => {
          throw new Error("Clipboard denied");
        },
      },
    },
  });
  await assert.rejects(write(pending), /Clipboard denied/);
  reject(new Error("Network failed"));
  await new Promise((resolve) => setImmediate(resolve));
});

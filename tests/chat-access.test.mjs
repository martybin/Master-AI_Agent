import test from "node:test";
import assert from "node:assert/strict";
import { mountChatAccess } from "../src/master_agent/web/chat-access.mjs";
test("in-chat access never applies without consent and clears scope between chats", async () => {
  const previous = globalThis.document,
    nodes = new Map(),
    calls = [];
  const get = (id) => {
    if (!nodes.has(id))
      nodes.set(id, {
        value: "",
        checked: false,
        handlers: {},
        addEventListener(n, f) {
          this.handlers[n] = f;
        },
        replaceChildren() {},
      });
    return nodes.get(id);
  };
  globalThis.document = {
    getElementById: get,
    createTextNode: (nodeValue) => ({ nodeValue }),
  };
  try {
    const state = { key: "owner", chat: null, busy: false };
    const ui = mountChatAccess({
      state,
      api: async (path, options) => {
        calls.push([path, options]);
        return { mode: "system", workspace: "/tmp/project" };
      },
      picker: { open() {} },
      ensureChat: async () => {
        state.chat = { id: "one" };
      },
      showError: (e) => {
        throw e;
      },
    });
    get("chat-folder").value = "/tmp/project";
    get("chat-scope").value = "system";
    await get("chat-access-form").handlers.submit({ preventDefault() {} });
    assert.equal(calls.length, 0);
    get("chat-consent").checked = true;
    await get("chat-access-form").handlers.submit({ preventDefault() {} });
    assert.equal(JSON.parse(calls[0][1].body).confirm, true);
    assert.equal(calls[0][0], "/local/grants/one");
    assert.equal(get("chat-consent").checked, false);
    ui.reset();
    assert.equal(get("chat-folder").value, "");
    assert.equal(get("chat-scope").value, "off");
    ui.busy(true);
    assert.equal(get("chat-access-save").disabled, true);
  } finally {
    globalThis.document = previous;
  }
});

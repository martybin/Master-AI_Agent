import test from "node:test";
import assert from "node:assert/strict";
import { mountFolderPicker } from "../src/master_agent/web/folder-picker.mjs";

class Node {
  constructor() {
    this.handlers = {};
    this.childNodes = [];
    this.value = "";
    this.checked = false;
    this.open = false;
  }
  addEventListener(name, action) {
    this.handlers[name] = action;
  }
  replaceChildren(...nodes) {
    this.childNodes = nodes;
  }
  append(...nodes) {
    this.childNodes.push(...nodes);
  }
  dispatchEvent(event) {
    this.handlers[event.type]?.(event);
  }
  close() {
    this.open = false;
    this.handlers.close?.();
  }
}
function setup(api) {
  const nodes = new Map();
  const get = (id) => {
    if (!nodes.has(id)) nodes.set(id, new Node());
    return nodes.get(id);
  };
  const previous = globalThis.document;
  globalThis.document = {
    getElementById: get,
    createTextNode: (nodeValue) => ({ nodeValue }),
  };
  const ui = mountFolderPicker({
    api,
    el: (tag, cls, text) => Object.assign(new Node(), { text }),
    button: (text, action) => {
      const node = new Node();
      node.addEventListener("click", action);
      return node;
    },
    showDialog: (id) => {
      get(id).open = true;
    },
    toast() {},
    requireConnection: () => true,
  });
  return {
    ui,
    get,
    restore: () => {
      globalThis.document = previous;
    },
  };
}
const listing = {
  path: "/tmp/My Projects",
  parent: "/tmp",
  entries: [
    { name: "New chat", directory: true, path: "/tmp/My Projects/New chat" },
  ],
  truncated: false,
};
test("Browse opens native directory picker immediately and cancellation leaves a usable in-app chooser", async () => {
  const calls = [];
  const { ui, get, restore } = setup(async (path, options) => {
    calls.push([path, options]);
    return path.startsWith("/local/browse")
      ? listing
      : { available: true, cancelled: true };
  });
  try {
    const input = get("access-folder");
    input.value = listing.path;
    await ui.open(input);
    assert.equal(get("folder-picker").open, true);
    assert(calls[1][0].startsWith("/local/pick-folder"));
    assert.equal(input.value, listing.path);
    assert.equal(get("folder-use").disabled, false);
    get("folder-use").handlers.click();
    assert.equal(input.value, listing.path);
    assert.equal(get("folder-picker").open, false);
    assert(calls.every(([path]) => !/grant|approve|execute/.test(path)));
    assert.equal(get("access-confirm").checked, false);
  } finally {
    restore();
  }
});
test("closing the folder dialog invalidates a late native result", async () => {
  let finish;
  const { ui, get, restore } = setup(async (path) =>
    path.startsWith("/local/browse")
      ? listing
      : new Promise((resolve) => {
          finish = resolve;
        }),
  );
  try {
    const input = get("access-folder");
    input.value = listing.path;
    const opened = ui.open(input);
    await Promise.resolve();
    await Promise.resolve();
    get("folder-picker").close();
    finish({ available: true, path: "/tmp/wrong-folder" });
    await opened;
    assert.equal(input.value, listing.path);
  } finally {
    restore();
  }
});

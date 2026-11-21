import test from "node:test";
import assert from "node:assert/strict";
import { mountCoding } from "../src/master_agent/web/coding-ui.mjs";

test("permission dialog is explicit, restores pending requests and never self-approves", async () => {
  const nodes = new Map();
  const previous = globalThis.document;
  globalThis.document = {
    createTextNode: (value) => ({ nodeValue: value }),
    getElementById(id) {
      if (!nodes.has(id))
        nodes.set(id, {
          addEventListener() {},
          checked: true,
          replaceChildren(node) {
            this.textContent = node.nodeValue;
          },
        });
      return nodes.get(id);
    },
  };
  try {
    let opened = 0;
    let status = "pending";
    const calls = [];
    const ui = mountCoding({
      api: async (path, options) => {
        calls.push([path, options]);
        return {
          id: "request-1",
          status,
          workspace: "/tmp/codec",
          mode: "system",
          reason: "Write codec",
        };
      },
      state: {},
      requireConnection: () => true,
      showDialog: () => {
        opened++;
      },
      showError: (error) => {
        throw error;
      },
    });
    await ui.openAccess({ id: "request-1" }, { pendingOnly: true });
    assert.equal(opened, 1);
    assert.equal(nodes.get("access-folder").value, "/tmp/codec");
    assert.equal(nodes.get("access-confirm").checked, false);
    assert.equal(nodes.get("access-approve").disabled, false);
    assert(calls.every(([, options]) => options === undefined));
    status = "approved";
    await ui.openAccess({ id: "request-1" }, { pendingOnly: true });
    assert.equal(opened, 1); // Reopening history must not pop up an already approved request.
    await ui.openAccess({ id: "request-1" });
    assert.equal(opened, 2);
    assert.equal(nodes.get("access-approve").disabled, true);
    assert.equal(nodes.get("access-resume").hidden, false);
  } finally {
    globalThis.document = previous;
  }
});

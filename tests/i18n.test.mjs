import test from "node:test";
import assert from "node:assert/strict";
import {
  t,
  msg,
  number,
  dateTime,
  setLanguage,
  getLanguage,
  setText,
  setAttr,
  LANGUAGE_KEY,
} from "../src/master_agent/web/i18n.mjs?v=0.5.0";
import { fa } from "../src/master_agent/web/translations-fa.mjs?v=0.5.0";

function element() {
  return {
    childNodes: [],
    values: {},
    setAttribute(name, value) {
      this.values[name] = value;
    },
    replaceChildren(...children) {
      this.childNodes = children;
    },
  };
}
test("live switching is reversible, persists, and never translates owner or model text", () => {
  const previous = {
    document: globalThis.document,
    storage: globalThis.localStorage,
  };
  const root = element(),
    label = element(),
    owner = element(),
    model = element(),
    code = element(),
    path = element();
  root.childNodes = [label, owner, model, code, path];
  const saved = new Map();
  globalThis.localStorage = { setItem: (key, value) => saved.set(key, value) };
  globalThis.document = {
    documentElement: root,
    createTextNode: (nodeValue) => ({ nodeValue }),
    getElementById: () => null,
  };
  try {
    setLanguage("en");
    setText(label, "Choose where to code");
    setAttr(label, "aria-label", "Choose a folder");
    for (const node of [owner, model, code, path])
      setText(node, "New chat", false);
    setLanguage("fa");
    assert.equal(root.lang, "fa");
    assert.equal(root.dir, "rtl");
    assert.equal(label.childNodes[0].nodeValue, "انتخاب محل کدنویسی");
    assert.equal(label.values["aria-label"], "انتخاب پوشه");
    assert.equal(saved.get(LANGUAGE_KEY), "fa");
    for (const node of [owner, model, code, path])
      assert.equal(node.childNodes[0].nodeValue, "New chat");
    setLanguage("en");
    assert.equal(root.dir, "ltr");
    assert.equal(label.childNodes[0].nodeValue, "Choose where to code");
    setLanguage("invalid");
    assert.equal(getLanguage(), "en");
    // Missing/blocked browser storage must not prevent switching.
    globalThis.localStorage = {
      setItem() {
        throw new Error("blocked");
      },
    };
    setLanguage("fa");
    assert.equal(root.dir, "rtl");
  } finally {
    globalThis.document = previous.document;
    globalThis.localStorage = previous.storage;
    setLanguage("en", { persist: false });
  }
});

test("templates translate nested status labels while preserving user slots and HTML as text", () => {
  const value = msg("{name} · {status} · {id}", {
    name: "New chat <script>",
    status: msg("Active"),
    id: "abc",
  });
  assert.equal(t(value, "fa"), "New chat <script> · فعال · abc");
  assert.equal(t(value, "en"), "New chat <script> · Active · abc");
  assert.equal(t("  Workspace key \n", "fa"), "  کلید فضای کاری \n");
  assert.equal(t("Unknown service detail", "fa"), "Unknown service detail");
  assert.equal(t("constructor", "fa"), "constructor");
});

test("Persian catalog preserves every interpolation slot", () => {
  const slots = (value) =>
    [...value.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
  for (const [key, value] of Object.entries(fa)) {
    assert(value.trim(), key);
    assert.deepEqual(slots(key), slots(value), key);
  }
});

test("numbers and times update with the interface without rewriting stored values", () => {
  assert.equal(t(number(12), "en"), "12");
  assert.equal(t(number(12), "fa"), "۱۲");
  const time = dateTime("2026-11-22T12:30:00Z", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    hourCycle: "h23",
  });
  assert.equal(t(time, "en"), "12:30");
  assert.equal(t(time, "fa"), "۱۲:۳۰");
});

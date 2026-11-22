import { fa } from "./translations-fa.mjs?v=0.4.4";

export const LANGUAGE_KEY = "biscode-language";
let language = "en";
try {
  const saved = globalThis.localStorage?.getItem(LANGUAGE_KEY);
  language = saved === "fa" || saved === "en" ? saved : "en";
} catch {
  /* Storage is optional. */
}
const bindings = new WeakMap();
const attributes = new WeakMap();
export const getLanguage = () => language;
export const locale = () => (language === "fa" ? "fa-IR" : "en-US");
export const msg = (key, params = {}) => ({ key, params });
export const number = (value, options = {}) => ({
  kind: "number",
  value,
  options,
});
export const dateTime = (value, options = {}) => ({
  kind: "dateTime",
  value,
  options,
});
export function t(source, selected = language) {
  const lang = selected === "fa" ? "fa-IR" : "en-US";
  if (source?.kind === "number")
    return new Intl.NumberFormat(lang, source.options).format(source.value);
  if (source?.kind === "dateTime")
    return new Intl.DateTimeFormat(lang, source.options).format(
      new Date(source.value),
    );
  const { key, params } =
    typeof source === "object" && source !== null
      ? source
      : { key: String(source ?? ""), params: {} };
  const normalized = key.replace(/\s+/g, " ").trim();
  const translated = Object.hasOwn(fa, key)
    ? fa[key]
    : Object.hasOwn(fa, normalized)
      ? fa[normalized]
      : undefined;
  const template =
    selected === "fa" && translated !== undefined
      ? Object.hasOwn(fa, key)
        ? translated
        : key.match(/^\s*/)[0] + translated + key.match(/\s*$/)[0]
      : key;
  return template.replace(/\{(\w+)\}/g, (whole, name) =>
    Object.hasOwn(params, name)
      ? typeof params[name] === "object"
        ? t(params[name], selected)
        : String(params[name])
      : whole,
  );
}
export function textNode(source, translate = true) {
  const node = document.createTextNode(translate ? t(source) : String(source));
  if (translate) bindings.set(node, source);
  return node;
}
export function setText(node, source, translate = true) {
  node.replaceChildren(textNode(source, translate));
}
export function setAttr(node, name, source) {
  const values = attributes.get(node) || {};
  values[name] = source;
  attributes.set(node, values);
  node.setAttribute(name, t(source));
}
function apply(root) {
  if (bindings.has(root)) root.nodeValue = t(bindings.get(root));
  if (attributes.has(root))
    for (const [name, source] of Object.entries(attributes.get(root)))
      root.setAttribute(name, t(source));
  for (const child of root.childNodes || []) apply(child);
}
export function setLanguage(value, { persist = true } = {}) {
  if (value !== "fa" && value !== "en") return;
  language = value;
  if (persist) {
    try {
      globalThis.localStorage?.setItem(LANGUAGE_KEY, value);
    } catch {
      /* Optional. */
    }
  }
  if (typeof document === "undefined") return;
  document.documentElement.lang = value;
  document.documentElement.dir = value === "fa" ? "rtl" : "ltr";
  apply(document.documentElement);
  const select = document.getElementById("interface-language");
  if (select) select.value = value;
}
// Bind only the initial developer-owned HTML. Chat, code and owner data are never scanned.
export function initializeLanguage() {
  function scan(node) {
    if (node.nodeType === 3) {
      const key = node.nodeValue.replace(/\s+/g, " ").trim();
      if (Object.hasOwn(fa, key)) bindings.set(node, node.nodeValue);
    } else if (node.nodeType === 1) {
      if (node.matches('script, style, pre, code, [translate="no"]')) return;
      for (const name of ["title", "placeholder", "aria-label", "data-prompt"])
        if (
          node.hasAttribute(name) &&
          Object.hasOwn(fa, node.getAttribute(name))
        )
          setAttr(node, name, node.getAttribute(name));
    }
    for (const child of node.childNodes || []) scan(child);
  }
  scan(document.documentElement);
  setLanguage(language, { persist: false });
  document
    .getElementById("interface-language")
    .addEventListener("change", (event) => setLanguage(event.target.value));
}

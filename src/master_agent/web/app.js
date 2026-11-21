import { mountLocalAgent } from "./local-ui.mjs";
import { mountCoding } from "./coding-ui.mjs";
import {
  statuses,
  nodeKinds,
  stageLabels,
  warningLabels,
  EventParser,
  errorMessage,
  nodeDetail,
} from "./ui-utils.mjs";

const $ = (id) => document.getElementById(id);
const shapes = {
  master:
    '<path d="M12 2 15 9 22 12 15 15 12 22 9 15 2 12 9 9Z"/><path d="m12 8 1.5 2.5L16 12l-2.5 1.5L12 16l-1.5-2.5L8 12l2.5-1.5Z"/>',
  pin: '<path d="m16 3 5 5-4 2-3 6-2-2-7 7m2-14 6-3 2-4ZM7 7l10 10"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  chat: '<path d="M20 11.5a7.5 7.5 0 0 1-8 7.5H5l-3 3V11.5A7.5 7.5 0 0 1 10 4h2a7.5 7.5 0 0 1 8 7.5Z"/><path d="M7 9h8M7 13h5"/>',
  workflow:
    '<rect x="2" y="3" width="6" height="6" rx="1.5"/><rect x="16" y="15" width="6" height="6" rx="1.5"/><rect x="2" y="15" width="6" height="6" rx="1.5"/><path d="M5 9v6m3-9h5a6 6 0 0 1 6 6v3"/>',
  spark:
    '<path d="m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6ZM20 2v4m-2-2h4"/>',
  text: '<path d="M5 5h14M5 10h14M5 15h9M5 20h6"/>',
  branch:
    '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="20" r="2"/><path d="M6 7v11m0-3c0-7 12-1 12-7"/>',
  cube: '<path d="m12 2 9 5v10l-9 5-9-5V7ZM3 7l9 5 9-5m-9 5v10m-5-17 10 5"/>',
  shield:
    '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  book: '<path d="M12 5c-3-2-7-2-10-1v15c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1Zm0 0v15"/>',
  settings:
    '<path d="m9 3-1 3-3 1v4l2 2v3l3 2 2-1 3 1 3-2v-3l2-2V7l-3-1-1-3Z"/><circle cx="12" cy="11" r="3"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  panel:
    '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M14 4v16"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  model: '<path d="m12 2 9 5-9 5-9-5Zm-9 10 9 5 9-5M3 17l9 5 9-5"/>',
  play: '<path d="m8 4 12 8-12 8Z"/>',
  pause: '<path d="M8 4v16M16 4v16"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V4H4v13h4"/>',
  download: '<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>',
  link: '<path d="m9 15 6-6m-7 4-2 2a4 4 0 0 0 6 6l3-3m-6-7 3-3a4 4 0 0 1 6 6l-2 2"/>',
  external: '<path d="M13 3h8v8m0-8-12 12M9 3H3v18h18v-6"/>',
  refresh:
    '<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5m-4 8a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
  webhook:
    '<circle cx="12" cy="5" r="3"/><circle cx="5" cy="18" r="3"/><circle cx="19" cy="18" r="3"/><path d="m10.5 8-4 7m7-7 4 7M8 18h8"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
  sliders:
    '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',
  globe:
    '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
};
function icon(name) {
  const span = document.createElement("span");
  // Only constant, developer-owned SVG paths enter innerHTML. User/model strings use textContent.
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${shapes[name] || shapes.cube}</svg>`;
  return span;
}
function hydrateIcons(root = document) {
  root
    .querySelectorAll("[data-icon]")
    .forEach((node) =>
      node.replaceChildren(...icon(node.dataset.icon).childNodes),
    );
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(text, action, className = "secondary-button", glyph) {
  const node = el("button", className);
  node.type = "button";
  if (glyph) node.append(icon(glyph));
  node.append(document.createTextNode(text));
  node.addEventListener("click", action);
  return node;
}
function safelyRead(key) {
  try {
    return key === "master-chat"
      ? localStorage.getItem(key) || sessionStorage.getItem(key)
      : sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
function safelyStore(key, value) {
  try {
    const storage = key === "master-chat" ? localStorage : sessionStorage;
    if (value === null) storage.removeItem(key);
    else storage.setItem(key, value);
    if (key === "master-chat") sessionStorage.removeItem(key);
  } catch {
    /* Tab storage is optional. */
  }
}
const state = {
  key: safelyRead("master-session") || "",
  epoch: 0,
  chat: null,
  chats: [],
  builds: [],
  config: null,
  system: null,
  selectedBuild: null,
  inspectorTab: "flow",
  busy: false,
  busyChat: null,
  review: null,
  pendingSend: false,
  streamController: null,
  deletingChat: null,
};
let toastTimer, elapsedTimer, pollTimer, codingUI;
const faNumber = (value) => new Intl.NumberFormat("fa-IR").format(value);
const time = (value) =>
  new Intl.DateTimeFormat("fa-IR", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 5500);
}
function showDialog(id) {
  if (!$(id).open) $(id).showModal();
}
function showError(error) {
  if (error.name !== "AbortError" && error.message !== "session_changed")
    toast(errorMessage(error));
}
async function api(path, options = {}) {
  const epoch = state.epoch;
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${state.key}`,
      ...options.headers,
    },
  });
  if (state.epoch !== epoch) throw new Error("session_changed");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error("api_error");
    error.status = response.status;
    error.detail = data.detail;
    throw error;
  }
  return data;
}
function requireConnection() {
  if (state.key) return true;
  showDialog("settings-dialog");
  $("api-key").focus();
  return false;
}
function sidebar(open) {
  $("app-shell").classList.toggle("sidebar-open", open);
  $("sidebar-scrim").hidden = !open;
  $("sidebar").inert = matchMedia("(max-width:700px)").matches && !open;
}
function inspector(open) {
  $("inspector").hidden = !open;
  $("app-shell").classList.toggle("with-inspector", open);
}
function setBusy(busy) {
  state.busy = busy;
  $("send-button").disabled = busy;
  $("build-target").disabled = busy;
  $("cancel-run").hidden = !busy;
  $("build-progress").hidden = !busy;
  clearInterval(elapsedTimer);
  if (busy) {
    const started = Date.now();
    $("elapsed").textContent = "۰:۰۰";
    elapsedTimer = setInterval(() => {
      const seconds = Math.floor((Date.now() - started) / 1000);
      $("elapsed").textContent =
        `${faNumber(Math.floor(seconds / 60))}:${faNumber(seconds % 60).padStart(2, "۰")}`;
    }, 1000);
  }
}
function scrollBottom() {
  requestAnimationFrame(() => {
    $("conversation").scrollTop = $("conversation").scrollHeight;
  });
}
function setStage(stage, attempt) {
  $("progress-label").textContent =
    (stageLabels[stage] || stageLabels.thinking) +
    (attempt > 1 ? ` (تلاش ${faNumber(attempt)})` : "");
}
function renderHistory() {
  const container = $("chat-history");
  container.replaceChildren();
  const query = $("history-search").value.trim().toLocaleLowerCase();
  const chats = state.chats.filter((chat) =>
    chat.title.toLocaleLowerCase().includes(query),
  );
  if (!chats.length)
    container.append(
      el(
        "p",
        "history-empty",
        query
          ? "گفتگویی پیدا نشد."
          : state.key
            ? "اولین گفتگو را شروع کن؛ اینجا ذخیره می‌شود."
            : "گفتگوها بعد از اتصال اینجا نمایش داده می‌شوند.",
      ),
    );
  let group;
  for (const chat of chats) {
    const nextGroup = chat.pinned ? "پین‌شده‌ها" : "گفتگوهای قبلی";
    if (group !== nextGroup) {
      container.append(el("div", "history-group", nextGroup));
      group = nextGroup;
    }
    const row = el(
      "div",
      "history-row" + (state.chat?.id === chat.id ? " active" : ""),
    );
    const item = button(
      chat.title,
      () => loadChat(chat.id).catch(showError),
      "history-item",
      chat.pinned ? "pin" : "chat",
    );
    item.title = chat.title;
    if (state.chat?.id === chat.id) item.setAttribute("aria-current", "page");
    const text = item.lastChild;
    text.replaceWith(el("span", "history-title", text.textContent));
    const actions = el("div", "history-actions");
    const pin = button(
      "",
      async () => {
        pin.disabled = true;
        try {
          await api(`/chats/${encodeURIComponent(chat.id)}/pin`, {
            method: "PUT",
            body: JSON.stringify({ pinned: !chat.pinned }),
          });
          await refreshLists();
        } catch (error) {
          showError(error);
        } finally {
          pin.disabled = false;
        }
      },
      "icon-button history-action",
      "pin",
    );
    pin.title = chat.pinned ? "برداشتن پین" : "پین کردن گفتگو";
    pin.setAttribute("aria-label", `${pin.title}: ${chat.title}`);
    pin.setAttribute("aria-pressed", String(Boolean(chat.pinned)));
    const remove = button(
      "",
      () => {
        state.deletingChat = chat.id;
        $("delete-chat-title").textContent = chat.title;
        $("delete-chat-error").hidden = true;
        showDialog("delete-chat-dialog");
      },
      "icon-button history-action delete-chat",
      "trash",
    );
    remove.title = "حذف گفتگو";
    remove.setAttribute("aria-label", `حذف گفتگو: ${chat.title}`);
    remove.disabled = chat.status === "busy" || state.busyChat === chat.id;
    if (remove.disabled) remove.title = "برای حذف، ابتدا کار جاری را متوقف کن";
    actions.append(pin, remove);
    row.append(item, actions);
    container.append(row);
  }
}
async function refreshLists() {
  if (!state.key) return;
  const [history, builds] = await Promise.all([api("/chats"), api("/builds")]);
  state.chats = history.chats;
  state.builds = builds.builds;
  $("workflow-count").textContent = faNumber(state.builds.length);
  renderHistory();
}
function newChat() {
  clearTimeout(pollTimer);
  state.chat = null;
  state.selectedBuild = null;
  safelyStore("master-chat", null);
  $("chat-title").textContent = "گفتگوی تازه";
  $("welcome").hidden = false;
  $("messages").replaceChildren();
  $("prompt").value = "";
  inspector(false);
  sidebar(false);
  renderHistory();
  $("prompt").focus();
}
async function loadChat(id) {
  const chat = await api(`/chats/${encodeURIComponent(id)}`);
  state.chat = chat;
  $("build-target").value = chat.target || "auto";
  safelyStore("master-chat", id);
  $("chat-title").textContent = chat.title;
  sidebar(false);
  renderHistory();
  renderMessages();
  const lastBuild = [...chat.messages]
    .reverse()
    .find((message) => message.build_id)?.build_id;
  if (lastBuild) await selectBuild(lastBuild);
  else {
    state.selectedBuild = null;
    inspector(false);
  }
  if (chat.status === "busy" && state.busyChat !== id) {
    setBusy(true);
    setStage("thinking");
    schedulePoll(id);
  } else if (!state.busyChat) setBusy(false);
}
function schedulePoll(id) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (!state.key || state.chat?.id !== id) return;
    try {
      await loadChat(id);
      if (state.chat.status !== "busy") {
        setBusy(false);
        await refreshLists();
      }
    } catch (error) {
      setBusy(false);
      showError(error);
    }
  }, 2000);
}
function renderMessages() {
  const messages = state.chat?.messages || [];
  $("welcome").hidden = messages.length > 0;
  const root = $("messages");
  root.replaceChildren();
  for (const message of messages) {
    const article = el("article", `message ${message.role} ${message.status}`);
    if (message.role === "assistant") {
      const avatar = el("div", "message-avatar");
      avatar.append(icon("master"));
      article.append(avatar);
    }
    const body = el("div", "message-body");
    body.append(
      el(
        "div",
        "message-role",
        message.role === "assistant" ? "BisCode" : "تو",
      ),
    );
    const content = el("div", "message-content");
    if (message.status === "pending") {
      const dots = el("span", "typing-dots");
      dots.setAttribute("aria-label", "در حال پاسخ‌دادن");
      for (let i = 0; i < 3; i++) dots.append(el("i"));
      content.append(dots);
    } else if (message.role === "assistant")
      renderMarkdown(content, message.content);
    else content.textContent = message.content;
    body.append(content);
    codingUI?.renderMessage(message, body);
    if (message.build_id) {
      const summary = state.builds.find(
        (build) => build.id === message.build_id,
      );
      const card = button(
        "",
        () => selectBuild(message.build_id).catch(showError),
        "message-build-card",
      );
      card.append(icon("workflow"));
      const text = el("div");
      text.append(
        el("strong", "", summary?.name || "طرح Workflow آماده است"),
        el(
          "small",
          "",
          summary
            ? `${faNumber(summary.node_count)} گره · ${statuses[summary.status] || summary.status}`
            : "بازکردن طرح و جزئیات",
        ),
      );
      card.append(text, el("span", "build-card-label", "دیدن طرح"));
      body.append(card);
    }
    if (message.role === "assistant" && message.status !== "pending") {
      const actions = el("div", "message-actions");
      const copy = button(
        "",
        () => copyText(message.content),
        "icon-button",
        "copy",
      );
      copy.title = "کپی پاسخ";
      copy.setAttribute("aria-label", "کپی پاسخ");
      actions.append(
        copy,
        el("span", "message-time", time(message.created_at)),
      );
      body.append(actions);
    }
    article.append(body);
    root.append(article);
  }
  scrollBottom();
}
function renderMarkdown(root, text) {
  root.classList.add("markdown");
  function inline(parent, value) {
    const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;
    let offset = 0;
    for (const match of value.matchAll(pattern)) {
      parent.append(document.createTextNode(value.slice(offset, match.index)));
      const bold = match[0].startsWith("**");
      parent.append(
        el(
          bold ? "strong" : "code",
          "",
          match[0].slice(bold ? 2 : 1, bold ? -2 : -1),
        ),
      );
      offset = match.index + match[0].length;
    }
    parent.append(document.createTextNode(value.slice(offset)));
  }
  const lines = text.split("\n");
  let paragraph = [],
    list = null;
  const flush = () => {
    if (paragraph.length) {
      const p = el("p");
      inline(p, paragraph.join("\n"));
      root.append(p);
      paragraph = [];
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trimStart().startsWith("```")) {
      flush();
      list = null;
      const code = [];
      while (++i < lines.length && !lines[i].trimStart().startsWith("```"))
        code.push(lines[i]);
      const pre = el("pre");
      pre.append(el("code", "", code.join("\n")));
      root.append(pre);
      continue;
    }
    const item = /^\s*(?:([-*])|([0-9۰-۹]+)[.)])\s+(.+)$/.exec(line);
    if (item) {
      flush();
      const tag = item[1] ? "UL" : "OL";
      if (!list || list.tagName !== tag) {
        list = el(tag.toLowerCase());
        root.append(list);
      }
      const li = el("li");
      inline(li, item[3]);
      list.append(li);
    } else {
      list = null;
      if (!line.trim()) flush();
      else if (/^#{1,4}\s/.test(line)) {
        flush();
        const heading = el("p", "markdown-heading");
        inline(heading, line.replace(/^#{1,4}\s+/, ""));
        root.append(heading);
      } else paragraph.push(line);
    }
  }
  flush();
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("کپی شد.");
  } catch {
    toast("مرورگر اجازهٔ کپی نداد. متن را انتخاب و کپی کن.");
  }
}
async function selectBuild(id) {
  const build = await api(`/builds/${encodeURIComponent(id)}`);
  state.selectedBuild = build;
  state.inspectorTab = "flow";
  renderInspector();
  inspector(true);
}
function badge(status) {
  return el("span", `status-badge ${status}`, statuses[status] || status);
}
function renderFlow(ir) {
  const canvas = el("div", "flow-canvas");
  const nodes = new Map(ir.nodes.map((node) => [node.id, node]));
  const targets = new Set(ir.edges.map((edge) => edge.target));
  const seen = new Set();
  function visit(id, parent, port) {
    if (seen.has(id) || !nodes.has(id)) return;
    seen.add(id);
    const node = nodes.get(id);
    if (parent)
      canvas.append(
        el(
          "div",
          "flow-connector",
          port === "true"
            ? "اگر شرط برقرار باشد"
            : port === "false"
              ? "در غیر این صورت"
              : "",
        ),
      );
    const card = el("div", "flow-node");
    card.dataset.kind = node.kind;
    const glyph = el("span", "flow-node-icon");
    glyph.append(icon((nodeKinds[node.kind] || [node.kind, "cube"])[1]));
    const info = el("div");
    info.append(
      el(
        "strong",
        "",
        (nodeKinds[node.kind] || [node.node_type || node.kind])[0],
      ),
      el("small", "", nodeDetail(node)),
      el("small", "flow-node-id", node.id),
    );
    if (node.kind === "ollama_chat") {
      const details = el("details", "node-instructions");
      details.append(
        el("summary", "", "دستورالعمل مدل"),
        el("p", "", node.system_prompt),
      );
      info.append(details);
    }
    card.append(glyph, info);
    canvas.append(card);
    for (const edge of ir.edges
      .filter((edge) => edge.source === id)
      .sort((a, b) => (a.port === "false") - (b.port === "false")))
      visit(edge.target, id, edge.port);
  }
  for (const node of ir.nodes.filter((node) => !targets.has(node.id)))
    visit(node.id, null, null);
  for (const node of ir.nodes)
    if (!seen.has(node.id)) visit(node.id, null, null);
  return canvas;
}
function actionButton(text, task, glyph, primary = false) {
  const node = button(
    text,
    async () => {
      node.disabled = true;
      try {
        await task();
      } catch (error) {
        showError(error);
        if (state.selectedBuild && state.key) {
          const id = state.selectedBuild.id;
          await selectBuild(id).catch(() => {});
        }
      } finally {
        node.disabled = false;
      }
    },
    primary ? "primary-button" : "secondary-button",
    glyph,
  );
  return node;
}
function renderInspector() {
  const root = $("inspector-content");
  root.replaceChildren();
  const build = state.selectedBuild;
  if (!build) {
    const empty = el("div", "inspector-empty");
    empty.append(
      icon("workflow"),
      el("h2", "", "اینجا، ایده شکل می‌گیرد."),
      el("p", "", "بعد از ساخت، گره‌ها و اتصال‌های Workflow را اینجا می‌بینی."),
    );
    root.append(empty);
    return;
  }
  const heading = el("section", "blueprint-heading");
  heading.append(
    badge(build.status),
    el("h2", "", build.ir.name),
    el(
      "p",
      "",
      `${faNumber(build.ir.nodes.length)} گره · ${faNumber(build.ir.edges.length)} اتصال · n8n Workflow`,
    ),
  );
  root.append(heading);
  const tabs = el("div", "blueprint-tabs");
  tabs.setAttribute("role", "tablist");
  for (const [value, text] of [
    ["flow", "طرح"],
    ["json", "JSON"],
  ]) {
    const tab = button(
      text,
      () => {
        state.inspectorTab = value;
        renderInspector();
      },
      state.inspectorTab === value ? "active" : "",
    );
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(state.inspectorTab === value));
    tabs.append(tab);
  }
  root.append(tabs);
  if (state.inspectorTab === "flow") root.append(renderFlow(build.ir));
  else
    root.append(
      el("pre", "json-view", JSON.stringify(build.workflow, null, 2)),
    );
  const actions = el("div", "blueprint-actions");
  if (build.status === "draft") {
    const send = actionButton(
      "ارسال به n8n",
      async () => {
        const result = await api(`/builds/${build.id}/deploy`, {
          method: "POST",
        });
        state.selectedBuild = result;
        await refreshLists();
        renderInspector();
        renderMessages();
        toast("Workflow در n8n ساخته شد؛ هنوز غیرفعال است.");
      },
      "workflow",
      true,
    );
    send.disabled = !state.system?.n8n?.api_authenticated;
    actions.append(send);
    actions.append(
      el(
        "p",
        "action-note",
        send.disabled
          ? "برای ارسال، ابتدا اتصال n8n را کامل کن."
          : "به‌صورت غیرفعال ساخته می‌شود.",
      ),
    );
    if (send.disabled)
      actions.append(
        button(
          "راهنمای اتصال n8n",
          () => showDialog("help-dialog"),
          "text-button",
          "link",
        ),
      );
  } else if (build.status === "deployed") {
    actions.append(
      actionButton(
        "بازبینی و فعال‌سازی",
        () => openReview(build.id),
        "shield",
        true,
      ),
    );
    actions.append(
      el(
        "p",
        "action-note",
        "فعال‌سازی بعد از بازبینی و تأیید تو انجام می‌شود.",
      ),
    );
  } else if (build.status === "active") {
    actions.append(
      actionButton(
        "غیرفعال‌کردن Workflow",
        async () => {
          state.selectedBuild = await api(`/builds/${build.id}/deactivate`, {
            method: "POST",
          });
          await refreshLists();
          renderInspector();
          renderMessages();
          toast("Workflow غیرفعال شد.");
        },
        "pause",
      ),
    );
  } else if (build.status.endsWith("_unknown")) {
    actions.append(
      el(
        "p",
        "action-note",
        "نتیجهٔ درخواست قبلی مشخص نیست؛ وضعیت را با n8n تطبیق بده.",
      ),
    );
    actions.append(
      button(
        "تطبیق وضعیت",
        () => {
          $("remote-id").value = build.n8n_workflow_id || "";
          $("remote-id").required = !build.n8n_workflow_id;
          showDialog("reconcile-dialog");
        },
        "primary-button",
        "refresh",
      ),
    );
  } else
    actions.append(
      button(
        "تازه‌کردن وضعیت",
        () => selectBuild(build.id).catch(showError),
        "secondary-button",
        "refresh",
      ),
    );
  actions.append(
    actionButton(
      "دریافت فایل JSON",
      async () => {
        const data = await api(`/builds/${build.id}/workflow`);
        const url = URL.createObjectURL(
          new Blob([JSON.stringify(data, null, 2)], {
            type: "application/json",
          }),
        );
        const link = document.createElement("a");
        link.href = url;
        link.download = `workflow-${build.id}.json`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      },
      "download",
    ),
  );
  if (build.n8n_workflow_id && state.config) {
    const link = el("a", "secondary-button", "بازکردن در n8n");
    link.href = `${state.config.n8n_editor_url}/workflow/${encodeURIComponent(build.n8n_workflow_id)}`;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.prepend(icon("external"));
    actions.append(link);
  }
  root.append(actions);
  const warnings = el("div", "blueprint-warnings");
  for (const warning of build.warnings || [])
    warnings.append(
      el("p", "", warningLabels[warning.code] || warning.message),
    );
  root.append(warnings);
  const meta = el("div", "blueprint-meta");
  meta.append(
    el("span", "", "IR 1.0 · اعتبارسنجی‌شده"),
    el("span", "", time(build.created_at)),
  );
  root.append(meta);
}
async function openReview(buildId) {
  const review = await api(`/builds/${buildId}/activation-review`, {
    method: "POST",
  });
  state.review = { ...review, buildId };
  $("approve-check").checked = false;
  $("approve-button").disabled = true;
  const root = $("review-content");
  root.replaceChildren();
  const box = el("div", "review-summary");
  box.append(
    el("strong", "", review.remote_workflow.name),
    el(
      "p",
      "",
      "با فعال‌شدن، Webhook یا زمان‌بندی این Workflow می‌تواند شروع به کار کند.",
    ),
  );
  const destinations = review.remote_workflow.nodes
    .filter((node) => node.parameters.url)
    .map((node) => node.parameters.url);
  for (const url of destinations)
    box.append(el("p", "", `مقصد درخواست: ${url}`));
  root.append(box);
  for (const warning of review.warnings)
    root.append(
      el("p", "service-detail", warningLabels[warning.code] || warning.message),
    );
  root.append(
    el(
      "p",
      "review-detail",
      `Version: ${review.remote_workflow.versionId}\nSHA-256: ${review.workflow_sha256}`,
    ),
  );
  const details = el("details");
  details.append(
    el("summary", "", "نمایش کامل نسخهٔ در حال تأیید"),
    el("pre", "json-view", JSON.stringify(review.remote_workflow, null, 2)),
  );
  root.append(details);
  showDialog("review-dialog");
}
function renderServices() {
  const root = $("service-list");
  root.replaceChildren();
  if (!state.key) return;
  const ollama = state.system?.ollama;
  const n8n = state.system?.n8n;
  for (const [name, good, label] of [
    [
      "Ollama",
      ollama?.model_installed,
      ollama?.model_installed ? "مدل آماده است" : "مدل در دسترس نیست",
    ],
    [
      "n8n",
      n8n?.api_authenticated,
      n8n?.api_authenticated
        ? "متصل"
        : n8n?.error === "not_configured"
          ? "کلید API تنظیم نشده"
          : "اتصال برقرار نیست",
    ],
  ]) {
    const row = el("div", "service-row");
    row.append(el("span", "", name), el("strong", good ? "good" : "", label));
    root.append(row);
  }
  if (state.config)
    root.append(
      el("p", "service-detail", `مدل انتخاب‌شده: ${state.config.model}`),
    );
}
async function refreshSystem() {
  const [config, system] = await Promise.all([
    api("/ui/config"),
    api("/system"),
  ]);
  state.config = config;
  state.system = system;
  $("model-label").textContent = config.model;
  $("profile-state").textContent = "فضای کاری متصل";
  const ready = system.ollama?.model_installed;
  $("connection-label").textContent = ready
    ? system.n8n?.api_authenticated
      ? "Ollama و n8n متصل‌اند"
      : "مدل آماده · n8n تنظیم نشده"
    : "مدل در دسترس نیست";
  $("connection-dot").className =
    `status-dot ${ready && system.n8n?.api_authenticated ? "connected" : "partial"}`;
  $("logout-button").hidden = false;
  renderServices();
  if (state.selectedBuild) renderInspector();
}
async function connect(event) {
  event.preventDefault();
  const key = $("api-key").value.trim();
  if (!key) return;
  $("connect-button").disabled = true;
  $("connection-error").hidden = true;
  state.epoch++;
  state.key = key;
  try {
    await refreshSystem();
    await refreshLists();
    safelyStore("master-session", $("remember-key").checked ? key : null);
    $("api-key").value = "";
    $("settings-dialog").close();
    toast("به فضای کاری متصل شدی.");
    if (state.pendingSend) {
      state.pendingSend = false;
      await submit();
    } else {
      await restoreChat();
    }
  } catch (error) {
    state.key = "";
    state.config = null;
    state.system = null;
    $("connection-error").textContent = errorMessage(error);
    $("connection-error").hidden = false;
  } finally {
    $("connect-button").disabled = false;
  }
}
function logout() {
  state.epoch++;
  state.streamController?.abort();
  state.key = "";
  state.config = null;
  state.system = null;
  state.chats = [];
  $("delete-chat-dialog").close();
  state.builds = [];
  state.busyChat = null;
  state.pendingSend = false;
  safelyStore("master-session", null);
  safelyStore("master-chat", null);
  clearTimeout(pollTimer);
  setBusy(false);
  newChat();
  $("workflow-count").textContent = "—";
  $("model-label").textContent = "Ollama · Local";
  $("connection-label").textContent = "اتصال به فضای کاری";
  $("connection-dot").className = "status-dot";
  $("profile-state").textContent = "اتصال به مدل محلی";
  $("logout-button").hidden = true;
  renderServices();
  $("settings-dialog").close();
  toast("اتصال این تب قطع شد.");
}
async function submit(event, resume = null) {
  event?.preventDefault();
  const content = resume ? resume.content : $("prompt").value.trim();
  if (!content || state.busy) return;
  if (!requireConnection()) {
    state.pendingSend = true;
    return;
  }
  const epoch = state.epoch;
  let chatId,
    started = false,
    finished = false;
  setBusy(true);
  setStage("thinking");
  try {
    if (!state.chat) {
      state.chat = await api("/chats", {
        method: "POST",
        body: JSON.stringify({ target: $("build-target").value }),
      });
      safelyStore("master-chat", state.chat.id);
    }
    chatId = state.chat.id;
    state.busyChat = chatId;
    const tempTime = new Date().toISOString();
    state.chat.messages.push(
      {
        id: "optimistic-user",
        role: "user",
        content,
        status: "complete",
        created_at: tempTime,
        optimistic: true,
      },
      {
        id: "optimistic-assistant",
        role: "assistant",
        content: "",
        status: "pending",
        created_at: tempTime,
        optimistic: true,
      },
    );
    $("prompt").value = "";
    $("prompt").style.height = "";
    renderMessages();
    sidebar(false);
    const controller = new AbortController();
    state.streamController = controller;
    const response = await fetch(
      `/api/v1/chats/${chatId}/${resume ? "resume" : "messages"}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${state.key}`,
        },
        body: JSON.stringify(
          resume
            ? { access_request_id: resume.id }
            : { content, request_id: crypto.randomUUID() },
        ),
        signal: controller.signal,
      },
    );
    if (state.epoch !== epoch) return;
    if (!response.ok) {
      const data = await response.json();
      const error = new Error("api_error");
      error.status = response.status;
      error.detail = data.detail;
      throw error;
    }
    if (response.headers.get("content-type")?.includes("application/json")) {
      await loadChat(chatId);
      finished = true;
      return;
    }
    const parser = new EventParser((event) => {
      if (state.epoch !== epoch) return;
      if (event.type === "started") {
        started = true;
        if (state.chat?.id === chatId) {
          state.chat.messages = state.chat.messages.filter(
            (item) => !item.optimistic,
          );
          state.chat.messages.push(event.user, event.assistant);
          $("chat-title").textContent = content.slice(0, 48);
          renderMessages();
        }
      } else if (event.type === "stage") {
        setStage(event.stage, event.attempt);
        if (event.tool)
          $("progress-label").textContent = "در حال اجرای ابزار: " + event.tool;
      } else if (event.type === "complete" || event.type === "failed") {
        finished = true;
        if (state.chat?.id === chatId) {
          state.chat.messages = state.chat.messages.filter(
            (item) => item.id !== event.message.id,
          );
          state.chat.messages.push(event.message);
          if (event.build) {
            state.chat.title = event.build.ir.name;
            $("chat-title").textContent = event.build.ir.name;
            state.selectedBuild = event.build;
            state.builds.unshift({
              id: event.build.id,
              name: event.build.ir.name,
              node_count: event.build.ir.nodes.length,
              status: event.build.status,
            });
            renderInspector();
            inspector(true);
          }
          renderMessages();
        }
      }
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        parser.push(decoder.decode());
        break;
      }
      parser.push(decoder.decode(value, { stream: true }));
    }
    if (!finished) throw new Error("stream_interrupted");
    await refreshLists();
  } catch (error) {
    if (state.epoch !== epoch || error.name === "AbortError") return;
    showError(error);
    if (chatId && started) {
      await loadChat(chatId).catch(() => {});
      if (state.chat?.status === "busy") schedulePoll(chatId);
    } else if (state.chat?.id === chatId) {
      state.chat.messages = state.chat.messages.filter(
        (item) => !item.optimistic,
      );
      $("prompt").value = content;
      renderMessages();
    }
  } finally {
    if (state.epoch === epoch) {
      state.busyChat = null;
      state.streamController = null;
      setBusy(state.chat?.status === "busy" && started && !finished);
      if (state.chat?.status === "busy" && !finished) schedulePoll(chatId);
      $("prompt").focus();
    }
  }
}

hydrateIcons();
$("composer").addEventListener("submit", submit);
$("prompt").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submit();
  }
});
$("prompt").addEventListener("input", () => {
  $("prompt").style.height = "auto";
  $("prompt").style.height = `${Math.min($("prompt").scrollHeight, 180)}px`;
});
$("new-chat").addEventListener("click", newChat);
$("menu-button").addEventListener("click", () => sidebar(true));
$("sidebar-scrim").addEventListener("click", () => sidebar(false));
$("toggle-inspector").addEventListener("click", () => {
  renderInspector();
  inspector($("inspector").hidden);
});
$("close-inspector").addEventListener("click", () => inspector(false));
$("toggle-search").addEventListener("click", () => {
  $("history-search").hidden = !$("history-search").hidden;
  if (!$("history-search").hidden) $("history-search").focus();
  else {
    $("history-search").value = "";
    renderHistory();
  }
});
$("history-search").addEventListener("input", renderHistory);
for (const id of ["settings-button", "connection-button"])
  $(id).addEventListener("click", () => {
    renderServices();
    showDialog("settings-dialog");
  });
$("help-button").addEventListener("click", () => showDialog("help-dialog"));
$("connection-form").addEventListener("submit", connect);
$("logout-button").addEventListener("click", logout);
$("refresh-system").addEventListener("click", async () => {
  if (!requireConnection()) return;
  try {
    await refreshSystem();
    toast("وضعیت اتصال‌ها تازه شد.");
  } catch (error) {
    showError(error);
  }
});
for (const node of document.querySelectorAll("[data-close]"))
  node.addEventListener("click", () => $(node.dataset.close).close());
for (const node of document.querySelectorAll("[data-prompt]"))
  node.addEventListener("click", () => {
    $("prompt").value = node.dataset.prompt;
    $("prompt").dispatchEvent(new Event("input"));
    $("prompt").focus();
  });
for (const dialog of document.querySelectorAll("dialog"))
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      const rect = dialog.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      )
        dialog.close();
    }
  });
$("review-dialog").addEventListener("close", () => {
  state.review = null;
  $("approve-check").checked = false;
  $("approve-button").disabled = true;
});
$("approve-check").addEventListener(
  "change",
  () => ($("approve-button").disabled = !$("approve-check").checked),
);
$("approve-button").addEventListener("click", async () => {
  if (!state.review || !$("approve-check").checked) return;
  const review = state.review;
  $("approve-button").disabled = true;
  try {
    state.selectedBuild = await api(`/builds/${review.buildId}/activate`, {
      method: "POST",
      body: JSON.stringify({
        confirm: true,
        workflow_sha256: review.workflow_sha256,
        review_sha256: review.review_sha256,
      }),
    });
    $("review-dialog").close();
    await refreshLists();
    renderInspector();
    renderMessages();
    toast("همین نسخه با تأیید تو فعال شد.");
  } catch (error) {
    $("review-dialog").close();
    showError(error);
    await selectBuild(review.buildId).catch(() => {});
  }
});
$("all-workflows").addEventListener("click", async () => {
  if (!requireConnection()) return;
  try {
    await refreshLists();
    const root = $("workflow-list");
    root.replaceChildren();
    if (!state.builds.length)
      root.append(
        el(
          "p",
          "list-empty",
          "هنوز Workflowای نساخته‌ای. از یک گفتگو شروع کن.",
        ),
      );
    for (const build of state.builds) {
      const item = button(
        "",
        () => {
          $("workflows-dialog").close();
          sidebar(false);
          selectBuild(build.id).catch(showError);
        },
        "workflow-list-item",
      );
      const info = el("div");
      info.append(
        el("strong", "", build.name),
        el(
          "small",
          "",
          `${faNumber(build.node_count)} گره · ${time(build.updated_at)}`,
        ),
      );
      item.append(icon("workflow"), info, badge(build.status));
      root.append(item);
    }
    showDialog("workflows-dialog");
  } catch (error) {
    showError(error);
  }
});
$("reconcile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const build = state.selectedBuild;
  if (!build) return;
  const submit = event.submitter;
  submit.disabled = true;
  try {
    const id = $("remote-id").value.trim();
    state.selectedBuild = await api(`/builds/${build.id}/reconcile`, {
      method: "POST",
      body: JSON.stringify({ workflow_id: id || null }),
    });
    $("reconcile-dialog").close();
    await refreshLists();
    renderInspector();
    toast("وضعیت واقعی از n8n خوانده شد.");
  } catch (error) {
    showError(error);
  } finally {
    submit.disabled = false;
  }
});
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    newChat();
  }
  if (event.key === "Escape") sidebar(false);
});
matchMedia("(max-width:700px)").addEventListener("change", () =>
  sidebar(false),
);
async function restoreChat() {
  const saved = safelyRead("master-chat");
  const id = state.chats.some((chat) => chat.id === saved)
    ? saved
    : state.chats[0]?.id;
  if (id) await loadChat(id);
}
$("delete-chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = state.deletingChat;
  if (!id) return;
  const control = $("confirm-delete-chat");
  control.disabled = true;
  $("delete-chat-error").hidden = true;
  try {
    await api(`/chats/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (state.chat?.id === id) newChat();
    else if (safelyRead("master-chat") === id) safelyStore("master-chat", null);
    state.deletingChat = null;
    $("delete-chat-dialog").close();
    await refreshLists();
    toast("گفتگو حذف شد.");
  } catch (error) {
    if (error.message !== "session_changed") {
      $("delete-chat-error").textContent =
        error.status === 409
          ? "برای حذف این گفتگو، ابتدا کار جاری را متوقف کن."
          : errorMessage(error);
      $("delete-chat-error").hidden = false;
    }
  } finally {
    control.disabled = false;
  }
});
$("delete-chat-dialog").addEventListener("close", () => {
  state.deletingChat = null;
});
async function boot() {
  sidebar(false);
  renderHistory();
  if (!state.key) return;
  try {
    await refreshSystem();
    await refreshLists();
    await restoreChat();
  } catch {
    logout();
  }
}
mountLocalAgent({
  api,
  state,
  el,
  button,
  showDialog,
  toast,
  showError,
  requireConnection,
  refreshLists,
  loadChat,
});
codingUI = mountCoding({
  api,
  state,
  el,
  button,
  showDialog,
  showError,
  toast,
  requireConnection,
  resume: async (record) => {
    if (state.busy) {
      toast("ابتدا کار جاری را متوقف یا تمام کن.");
      return;
    }
    await loadChat(record.chat_id);
    const original = state.chat.messages.find(
      (m) => m.id === record.message_id,
    );
    if (!original) {
      toast("درخواست اصلی در گفتگو پیدا نشد.");
      return;
    }
    await submit(null, { ...record, content: original.content });
  },
});
$("build-target").addEventListener("change", async () => {
  if (!state.chat || !state.key) return;
  try {
    state.chat = await api(`/chats/${state.chat.id}/target`, {
      method: "PUT",
      body: JSON.stringify({ target: $("build-target").value }),
    });
  } catch (error) {
    $("build-target").value = state.chat.target || "auto";
    showError(error);
  }
});
$("cancel-run").addEventListener("click", async () => {
  const id = state.busyChat || state.chat?.id;
  if (!id) return;
  try {
    await api(`/chats/${id}/cancel`, { method: "POST" });
  } catch (error) {
    showError(error);
  }
});
boot();

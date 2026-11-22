import { setText, msg, dateTime, number } from "./i18n.mjs?v=0.5.0";
// Owner-only connection and local capability controls. Secrets never enter chat history.
import { statuses } from "./ui-utils.mjs?v=0.5.0";

export function mountLocalAgent({
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
}) {
  const $ = (id) => document.getElementById(id);
  let current = null,
    loginAlias = null;
  const modes = {
    off: "Off",
    workspace: "Project folder",
    system: "System & terminal",
  };
  const kinds = {
    telegram_bot: "Telegram bot",
    telegram_user: "Personal account",
    http: "HTTP API",
  };
  const localStates = {
    configured: "Configured",
    connected: "Connected",
    stopped: "Stopped",
    running: "Listening",
    waiting: "Waiting for connection",
    needs_attention: "Needs attention",
    pending: "Pending",
    delivering: "Delivering",
    uncertain: "Uncertain delivery — review required",
    complete: "Complete",
    failed: "Failed",
    cancelled: "Cancelled",
  };
  const statusLabel = (value) => localStates[value] || value;
  function error(err) {
    setText(
      $("local-panel-error"),
      err.status === 409 && typeof err.detail === "string"
        ? err.detail
        : "Could not complete the operation. Check the connection, sign-in, and settings.",
    );
    $("local-panel-error").hidden = false;
    showError(err);
  }
  async function run(action) {
    $("local-panel-error").hidden = true;
    try {
      return await action();
    } catch (err) {
      error(err);
      return null;
    }
  }
  function option(value, text) {
    const node = el("option", "", text);
    node.value = value;
    return node;
  }
  async function refresh() {
    if (!state.key) return;
    await refreshLists();
    current = await api(
      "/local/status" +
        (state.chat ? "?chat_id=" + encodeURIComponent(state.chat.id) : ""),
    );
    setText(
      $("local-workspace"),
      msg("Workspace: {path}", { path: current.workspace }),
    );
    setText(
      $("local-current-grant"),
      msg("Chat access: {mode}{expiry}", {
        mode: msg(modes[current.access.mode]),
        expiry: current.access.expires_at
          ? msg(" · Expires: {time}", {
              time: dateTime(current.access.expires_at, {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              }),
            })
          : "",
      }),
    );
    $("local-access-mode").value = current.access.mode;
    $("local-workspace-input").value =
      current.access.workspace || current.workspace;
    setText(
      $("local-catalog-count"),
      msg("{count} node types available in the catalog.", {
        count: number(current.catalog_count),
      }),
    );
    $("local-tool-list").replaceChildren(
      ...current.tools.map((tool) => {
        const item = el("div", "local-tool");
        item.append(
          el("strong", "", tool.name),
          el(
            "span",
            "",
            tool.scope === "system"
              ? "System access required"
              : tool.scope === "workspace"
                ? "Workspace access required"
                : "Ready",
          ),
        );
        return item;
      }),
    );
    const list = $("local-connection-list");
    list.replaceChildren();
    if (!current.connections.length)
      list.append(el("p", "service-detail", "No connections yet."));
    for (const connection of current.connections) {
      const card = el("article", "local-connection-card");
      const info = el("div");
      info.append(
        el("strong", "", connection.label, false),
        el(
          "p",
          "service-detail",
          msg("{id} · {kind} · {status}", {
            id: connection.id,
            kind: msg(kinds[connection.kind]),
            status: msg(statusLabel(connection.status)),
          }),
        ),
      );
      card.append(info);
      const actions = el("div", "local-row-actions");
      actions.append(
        button(
          "Test connection",
          () =>
            run(async () => {
              await api(`/local/connections/${connection.id}/verify`, {
                method: "POST",
              });
              await refresh();
              toast("Connection checked.");
            }),
          "secondary-button",
        ),
      );
      if (connection.kind === "telegram_user")
        actions.append(
          button(
            "Sign in",
            () => {
              loginAlias = connection.id;
              $("telegram-password-fields").hidden = true;
              setText(
                $("telegram-login-title"),
                msg("Sign in to {label}", { label: connection.label }),
              );
              $("telegram-login-panel").hidden = false;
              $("telegram-phone-form").hidden = false;
              $("telegram-code-form").hidden = true;
            },
            "secondary-button",
          ),
        );
      card.append(actions);
      list.append(card);
    }
    const selectedConnection = $("binding-connection").value,
      selectedBuild = $("binding-build").value;
    $("binding-connection").replaceChildren(
      option("", "Select a connection"),
      ...current.connections
        .filter((c) => c.kind !== "http")
        .map((c) =>
          option(
            c.id,
            msg("{label} · {kind}", {
              label: c.label,
              kind: msg(kinds[c.kind]),
            }),
          ),
        ),
    );
    $("binding-build").replaceChildren(
      option("", "Select a workflow"),
      ...state.builds.map((b) =>
        option(
          b.id,
          msg("{name} · {status} · {id}", {
            name: b.name,
            status: msg(statuses[b.status] || b.status),
            id: b.id.slice(0, 8),
          }),
        ),
      ),
    );
    $("binding-connection").value = selectedConnection;
    $("binding-build").value = selectedBuild;
    const bindings = $("local-binding-list");
    bindings.replaceChildren();
    for (const binding of current.bindings) {
      const card = el("article", "local-connection-card");
      card.append(
        el("strong", "", binding.id),
        el(
          "p",
          "service-detail",
          msg("Status: {status}", { status: msg(statusLabel(binding.status)) }),
        ),
      );
      card.append(
        button(
          binding.enabled ? "Stop listening" : "Start listening",
          () =>
            run(async () => {
              if (binding.enabled)
                await api(`/local/bindings/${binding.id}/stop`, {
                  method: "POST",
                });
              else {
                if (!$("binding-start-confirm").checked) {
                  toast(
                    "Confirm automatic processing before starting the listener.",
                  );
                  return;
                }
                await api(`/local/bindings/${binding.id}/start`, {
                  method: "POST",
                  body: JSON.stringify({ confirm: true }),
                });
              }
              await refresh();
            }),
          "secondary-button",
        ),
      );
      bindings.append(card);
    }
    const inbox = $("local-inbox");
    inbox.replaceChildren(el("h3", "", "Message deliveries"));
    if (
      current.inbox.some((item) =>
        ["pending", "uncertain"].includes(item.status),
      )
    ) {
      const label = el("label", "check-label");
      const approval = document.createElement("input");
      approval.type = "checkbox";
      approval.id = "delivery-retry-confirm";
      label.append(
        approval,
        el(
          "span",
          "",
          "I understand the previous operation may have succeeded and approve the risk of a duplicate message or action on retry.",
        ),
      );
      inbox.append(label);
    }
    for (const item of current.inbox) {
      const row = el("article", "local-connection-card");
      row.append(
        el(
          "p",
          "service-detail",
          msg("{connection} · {status}", {
            connection: item.connection,
            status: msg(statusLabel(item.status)),
          }),
        ),
      );
      if (["pending", "uncertain"].includes(item.status))
        row.append(
          button(
            "Confirm and retry",
            () =>
              run(async () => {
                if (
                  !document.getElementById("delivery-retry-confirm")?.checked
                ) {
                  toast(
                    "Acknowledge the risk of duplicate actions before retrying.",
                  );
                  return;
                }
                await api(`/local/inbox/${item.id}/retry`, {
                  method: "POST",
                  body: JSON.stringify({ confirm: true }),
                });
                await refresh();
              }),
            "secondary-button",
          ),
        );
      inbox.append(row);
    }
    $("local-audit").replaceChildren(
      el("h3", "", "Recent activity"),
      ...current.audit.slice(0, 20).map((item) =>
        el(
          "p",
          "service-detail",
          msg("{time} · {action} · {status}", {
            time: dateTime(item.at, {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            }),
            action: item.tool || item.action,
            status: msg(statusLabel(item.status)),
          }),
        ),
      ),
    );
  }
  $("local-agent-button").addEventListener("click", () => {
    if (!requireConnection()) return;
    showDialog("local-agent-dialog");
    run(refresh);
  });
  $("local-refresh").addEventListener("click", () => run(refresh));
  for (const tab of document.querySelectorAll("[data-local-tab]"))
    tab.addEventListener("click", () => {
      for (const t of document.querySelectorAll("[data-local-tab]"))
        t.classList.toggle("active", t === tab);
      for (const panel of document.querySelectorAll("[data-local-panel]"))
        panel.hidden = panel.dataset.localPanel !== tab.dataset.localTab;
    });
  $("local-grant-form").addEventListener("submit", (event) => {
    event.preventDefault();
    run(async () => {
      if (!state.chat) {
        const chat = await api("/chats", {
          method: "POST",
          body: JSON.stringify({ target: $("build-target").value }),
        });
        await loadChat(chat.id);
      }
      await api(`/local/grants/${state.chat.id}`, {
        method: "PUT",
        body: JSON.stringify({
          mode: $("local-access-mode").value,
          confirm: $("local-system-confirm").checked,
          hours: 4,
          workspace: $("local-workspace-input").value || null,
        }),
      });
      $("local-system-confirm").checked = false;
      await refresh();
      toast("Access updated for this chat.");
    });
  });
  function fields() {
    const kind = $("connector-kind").value;
    $("connector-user-fields").hidden = kind !== "telegram_user";
    $("connector-http-fields").hidden = kind !== "http";
    $("connector-token-fields").hidden = kind === "telegram_user";
  }
  $("connector-kind").addEventListener("change", fields);
  $("local-connection-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    run(async () => {
      const data = {
        id: $("connector-id").value.trim(),
        label: $("connector-label").value.trim(),
        kind: $("connector-kind").value,
        token: $("connector-token").value,
        api_id: $("connector-api-id").value
          ? Number($("connector-api-id").value)
          : null,
        api_hash: $("connector-api-hash").value,
        base_url: $("connector-base-url").value,
        header_name: $("connector-header").value,
      };
      await api("/local/connections", {
        method: "POST",
        body: JSON.stringify(data),
      });
      $("local-connection-form").reset();
      fields();
      await refresh();
      toast("Connection saved with encrypted credentials.");
    }).finally(() => (submit.disabled = false));
  });
  $("telegram-phone-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    run(async () => {
      const result = await api(`/local/connections/${loginAlias}/login/start`, {
        method: "POST",
        body: JSON.stringify({ phone: $("telegram-phone").value }),
      });
      $("telegram-phone").value = "";
      if (result.state === "authorized") {
        $("telegram-login-panel").hidden = true;
        await refresh();
      } else {
        $("telegram-phone-form").hidden = true;
        $("telegram-code-form").hidden = false;
      }
    }).finally(() => (submit.disabled = false));
  });
  $("telegram-code-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    run(async () => {
      const result = await api(
        `/local/connections/${loginAlias}/login/finish`,
        {
          method: "POST",
          body: JSON.stringify({
            code: $("telegram-login-code").value,
            password: $("telegram-login-password").value,
          }),
        },
      );
      $("telegram-login-code").value = "";
      $("telegram-login-password").value = "";
      if (result.state === "password_required")
        $("telegram-password-fields").hidden = false;
      else {
        $("telegram-login-panel").hidden = true;
        loginAlias = null;
        await refresh();
        toast("Personal account signed in.");
      }
    }).finally(() => (submit.disabled = false));
  });
  $("telegram-cancel-login").addEventListener("click", () =>
    run(async () => {
      if (loginAlias)
        await api(`/local/connections/${loginAlias}/login/cancel`, {
          method: "POST",
        });
      loginAlias = null;
      $("telegram-login-panel").hidden = true;
      $("telegram-phone").value = "";
      $("telegram-login-code").value = "";
      $("telegram-login-password").value = "";
    }),
  );
  $("local-binding-form").addEventListener("submit", (event) => {
    event.preventDefault();
    run(async () => {
      const chats = $("binding-chats")
        .value.split(",")
        .map((s) => s.trim())
        .filter(Boolean)
        .map(Number);
      if (chats.some((id) => !Number.isSafeInteger(id)))
        throw new Error("Invalid chat id");
      await api(`/local/bindings/${$("binding-connection").value}`, {
        method: "PUT",
        body: JSON.stringify({
          build_id: $("binding-build").value,
          allowed_chat_ids: chats,
          reply_path: $("binding-reply-path").value.trim() || null,
        }),
      });
      await refresh();
      toast("Binding saved. The listener has not been started.");
    });
  });
  $("load-telegram-dialogs").addEventListener("click", () =>
    run(async () => {
      const alias = $("binding-connection").value;
      if (!alias) return;
      const data = await api(`/local/connections/${alias}/dialogs`);
      $("telegram-dialog-list").replaceChildren(
        ...data.dialogs.map((dialog) => {
          const row = el("label", "check-label");
          const check = document.createElement("input");
          check.type = "checkbox";
          check.addEventListener("change", () => {
            const values = new Set(
              $("binding-chats")
                .value.split(",")
                .map((v) => v.trim())
                .filter(Boolean),
            );
            if (check.checked) values.add(String(dialog.id));
            else values.delete(String(dialog.id));
            $("binding-chats").value = [...values].join(", ");
          });
          row.append(check, el("span", "", dialog.title + " · " + dialog.id));
          return row;
        }),
      );
    }),
  );
  $("catalog-import-form").addEventListener("submit", (event) => {
    event.preventDefault();
    run(async () => {
      if (!state.chat) {
        toast("Create a chat and grant workspace access first.");
        return;
      }
      const result = await api("/local/tools/execute", {
        method: "POST",
        body: JSON.stringify({
          chat_id: state.chat.id,
          name: "catalog.import_file",
          arguments: { path: $("catalog-file").value },
        }),
      });
      await refresh();
      toast(`Imported ${result.result.imported} node types.`);
    });
  });
  $("local-agent-dialog").addEventListener("close", () => {
    for (const id of [
      "connector-token",
      "connector-api-hash",
      "telegram-phone",
      "telegram-login-code",
      "telegram-login-password",
    ])
      $(id).value = "";
    $("binding-start-confirm").checked = false;
    $("local-system-confirm").checked = false;
  });
}

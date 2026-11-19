// Owner-only connection and local capability controls. Secrets never enter chat history.
import { statuses } from "./ui-utils.mjs";

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
    off: "خاموش",
    workspace: "پوشهٔ پروژه",
    system: "سیستم و ترمینال",
  };
  const kinds = {
    telegram_bot: "بات تلگرام",
    telegram_user: "اکانت شخصی",
    http: "HTTP API",
  };
  const localStates = {
    configured: "ثبت شده",
    connected: "متصل",
    stopped: "متوقف",
    running: "در حال دریافت",
    waiting: "منتظر اتصال",
    needs_attention: "نیاز به بررسی",
    pending: "منتظر پردازش",
    delivering: "در حال تحویل",
    uncertain: "نتیجه نامشخص؛ بررسی لازم است",
    complete: "تکمیل شد",
    failed: "ناموفق",
    cancelled: "متوقف شد",
  };
  const statusLabel = (value) => localStates[value] || value;
  function error(err) {
    $("local-panel-error").textContent =
      err.status === 409 && typeof err.detail === "string"
        ? err.detail
        : "عملیات کامل نشد؛ اتصال، ورود یا تنظیمات را بررسی کن.";
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
    $("local-workspace").textContent = "پوشهٔ پروژه: " + current.workspace;
    $("local-current-grant").textContent =
      "دسترسی این گفتگو: " +
      modes[current.access.mode] +
      (current.access.expires_at
        ? " · پایان: " +
          new Date(current.access.expires_at).toLocaleTimeString("fa-IR")
        : "");
    $("local-access-mode").value = current.access.mode;
    $("local-catalog-count").textContent =
      `${current.catalog_count} نوع گره در کاتالوگ ثبت شده است.`;
    $("local-tool-list").replaceChildren(
      ...current.tools.map((tool) => {
        const item = el("div", "local-tool");
        item.append(
          el("strong", "", tool.name),
          el(
            "span",
            "",
            tool.scope === "system"
              ? "نیاز به دسترسی سیستم"
              : tool.scope === "workspace"
                ? "نیاز به مجوز پروژه"
                : "آماده",
          ),
        );
        return item;
      }),
    );
    const list = $("local-connection-list");
    list.replaceChildren();
    if (!current.connections.length)
      list.append(el("p", "service-detail", "هنوز اتصالی ثبت نشده است."));
    for (const connection of current.connections) {
      const card = el("article", "local-connection-card");
      const info = el("div");
      info.append(
        el("strong", "", connection.label),
        el(
          "p",
          "service-detail",
          `${connection.id} · ${kinds[connection.kind]} · ${statusLabel(connection.status)}`,
        ),
      );
      card.append(info);
      const actions = el("div", "local-row-actions");
      actions.append(
        button(
          "بررسی اتصال",
          () =>
            run(async () => {
              await api(`/local/connections/${connection.id}/verify`, {
                method: "POST",
              });
              await refresh();
              toast("اتصال بررسی شد.");
            }),
          "secondary-button",
        ),
      );
      if (connection.kind === "telegram_user")
        actions.append(
          button(
            "ورود به حساب",
            () => {
              loginAlias = connection.id;
              $("telegram-password-fields").hidden = true;
              $("telegram-login-title").textContent =
                "ورود به " + connection.label;
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
      option("", "اتصال را انتخاب کن"),
      ...current.connections
        .filter((c) => c.kind !== "http")
        .map((c) => option(c.id, c.label + " · " + kinds[c.kind])),
    );
    $("binding-build").replaceChildren(
      option("", "Workflow را انتخاب کن"),
      ...state.builds.map((b) =>
        option(
          b.id,
          `${b.name} · ${statuses[b.status] || b.status} · ${b.id.slice(0, 8)}`,
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
        el("p", "service-detail", `وضعیت: ${statusLabel(binding.status)}`),
      );
      card.append(
        button(
          binding.enabled ? "توقف دریافت پیام" : "شروع دریافت پیام",
          () =>
            run(async () => {
              if (binding.enabled)
                await api(`/local/bindings/${binding.id}/stop`, {
                  method: "POST",
                });
              else {
                if (!$("binding-start-confirm").checked) {
                  toast("ابتدا اجازهٔ پردازش خودکار را انتخاب کن.");
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
    inbox.replaceChildren(el("h3", "", "تحویل پیام‌ها"));
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
          "می‌دانم عملیات قبلی ممکن است انجام شده باشد؛ تکرار پیام یا عملیات در تلاش مجدد را تأیید می‌کنم.",
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
          `${item.connection} · ${statusLabel(item.status)}`,
        ),
      );
      if (["pending", "uncertain"].includes(item.status))
        row.append(
          button(
            "تلاش مجدد با تأیید",
            () =>
              run(async () => {
                if (
                  !document.getElementById("delivery-retry-confirm")?.checked
                ) {
                  toast("ابتدا تأیید احتمال تکرار عملیات را انتخاب کن.");
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
      el("h3", "", "فعالیت اخیر"),
      ...current.audit
        .slice(0, 20)
        .map((item) =>
          el(
            "p",
            "service-detail",
            `${new Date(item.at).toLocaleTimeString("fa-IR")} · ${item.tool || item.action} · ${item.status}`,
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
        const chat = await api("/chats", { method: "POST" });
        await loadChat(chat.id);
      }
      await api(`/local/grants/${state.chat.id}`, {
        method: "PUT",
        body: JSON.stringify({
          mode: $("local-access-mode").value,
          confirm: $("local-system-confirm").checked,
          hours: 4,
        }),
      });
      $("local-system-confirm").checked = false;
      await refresh();
      toast("دسترسی همین گفتگو تنظیم شد.");
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
      toast("اتصال به‌صورت رمز‌شده ثبت شد.");
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
        toast("ورود به حساب شخصی کامل شد.");
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
      toast("اتصال ثبت شد؛ دریافت پیام هنوز شروع نشده است.");
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
        toast("ابتدا یک گفتگو بساز و دسترسی پوشهٔ پروژه را فعال کن.");
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
      toast(`${result.result.imported} گره وارد شد.`);
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

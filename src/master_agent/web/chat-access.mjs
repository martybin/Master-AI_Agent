import { setText, msg } from "./i18n.mjs?v=0.5.0";
export function mountChatAccess({ api, state, picker, ensureChat, showError }) {
  const $ = (id) => document.getElementById(id);
  let generation = 0;
  function reset() {
    generation++;
    $("chat-folder").value = "";
    $("chat-scope").value = "off";
    $("chat-consent").checked = false;
    setText($("chat-access-status"), "Access off");
  }
  async function refresh() {
    const token = ++generation,
      id = state.chat?.id;
    if (!id || !state.key) {
      reset();
      return;
    }
    const grant = await api(`/local/grants/${id}`);
    if (token !== generation || state.chat?.id !== id) return;
    $("chat-folder").value = grant.workspace || "";
    $("chat-scope").value = grant.mode;
    $("chat-consent").checked = false;
    setText(
      $("chat-access-status"),
      grant.mode === "off"
        ? "Access off"
        : msg("Granted: {scope}", {
            scope: msg(
              grant.mode === "system"
                ? "Full access · current OS user"
                : "Project files only",
            ),
          }),
    );
  }
  $("chat-browse").addEventListener("click", () => {
    if (!state.busy) picker.open($("chat-folder"));
  });
  $("chat-scope").addEventListener("change", () => {
    $("chat-consent").checked = false;
  });
  $("chat-folder").addEventListener("input", () => {
    $("chat-consent").checked = false;
  });
  $("chat-access-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.busy) return;
    $("chat-access-save").disabled = true;
    try {
      const folder = $("chat-folder").value,
        mode = $("chat-scope").value,
        confirm = $("chat-consent").checked;
      if (mode !== "off" && (!folder || !confirm)) return;
      const selected = await ensureChat();
      const id = selected?.id || state.chat?.id;
      await api(`/local/grants/${id}`, {
        method: "PUT",
        body: JSON.stringify({
          workspace: folder || null,
          mode,
          confirm,
          hours: 4,
        }),
      });
      await refresh();
    } catch (error) {
      showError(error);
    } finally {
      $("chat-access-save").disabled = false;
    }
  });
  reset();
  return {
    refresh,
    reset,
    busy(value) {
      for (const id of [
        "chat-browse",
        "chat-folder",
        "chat-scope",
        "chat-access-save",
        "chat-consent",
      ])
        $(id).disabled = value;
    },
  };
}

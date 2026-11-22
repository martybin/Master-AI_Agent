import { setText } from "./i18n.mjs?v=0.5.0";
import { errorMessage } from "./ui-utils.mjs?v=0.5.0";

// This is an authenticated owner control, never a model tool or an access grant.
export function mountFolderPicker({
  api,
  el,
  button,
  showDialog,
  toast,
  requireConnection,
}) {
  const $ = (id) => document.getElementById(id);
  let target = null,
    listing = null,
    generation = 0,
    nativePending = false;
  function fail(error) {
    setText(
      $("folder-picker-error"),
      typeof error.detail === "string" ? error.detail : errorMessage(error),
    );
    $("folder-picker-error").hidden = false;
  }
  function apply(path) {
    if (!target || !$("folder-picker").open) return;
    target.value = path;
    target.dispatchEvent(new Event("input", { bubbles: true }));
    $("folder-picker").close();
    toast("Coding will run in this folder.");
  }
  async function load(path = "") {
    const token = ++generation;
    $("folder-picker-error").hidden = true;
    $("folder-use").disabled = true;
    listing = null;
    $("folder-picker-list").replaceChildren();
    const data = await api(
      "/local/browse?" +
        new URLSearchParams({ path, hidden: $("folder-hidden").checked }),
    );
    if (token !== generation || !$("folder-picker").open) return;
    listing = data;
    setText($("folder-picker-path"), data.path, false);
    $("folder-up").disabled = !data.parent;
    $("folder-use").disabled = false;
    const list = $("folder-picker-list");
    const folders = data.entries.filter((entry) => entry.directory);
    if (!folders.length)
      list.append(
        el(
          "p",
          "service-detail",
          "No visible folders here. Use this folder, go up, or show hidden folders.",
        ),
      );
    for (const entry of folders) {
      const row = button(
        "",
        () => load(entry.path).catch(fail),
        "folder-row",
        "workflow",
      );
      row.append(el("span", "folder-name", entry.name, false));
      row.dir = "auto";
      list.append(row);
    }
    if (data.truncated)
      list.append(el("p", "service-detail", "Showing the first 400 entries."));
  }
  async function native() {
    if (nativePending) return;
    nativePending = true;
    const token = generation;
    const owner = target;
    $("folder-system").disabled = true;
    try {
      const picked = await api(
        "/local/pick-folder?" +
          new URLSearchParams({ path: listing?.path || owner?.value || "" }),
        { method: "POST" },
      );
      // Closing/reopening/navigating invalidates an older native selection.
      if (owner !== target || token !== generation || !$("folder-picker").open)
        return;
      if (picked.path) apply(picked.path);
      else if (picked.available === false)
        fail({
          detail:
            "The system file picker is not available. Choose a folder in this list.",
        });
    } catch (error) {
      if (token === generation) fail(error);
    } finally {
      nativePending = false;
      $("folder-system").disabled = false;
    }
  }
  async function open(input) {
    if (!requireConnection()) return;
    target = input;
    $("folder-name").value = "";
    showDialog("folder-picker");
    try {
      await load(input.value.trim());
    } catch {
      try {
        await load();
      } catch (error) {
        fail(error);
      }
    }
    // Open the OS folder dialog immediately; the in-app browser remains as a fallback.
    if ($("folder-picker").open) await native();
  }
  $("access-browse").addEventListener("click", () => open($("access-folder")));
  $("local-workspace-browse").addEventListener("click", () =>
    open($("local-workspace-input")),
  );
  $("folder-up").addEventListener("click", () => {
    if (listing?.parent) load(listing.parent).catch(fail);
  });
  $("folder-home").addEventListener("click", () => load().catch(fail));
  $("folder-hidden").addEventListener("change", () =>
    load(listing?.path || "").catch(fail),
  );
  $("folder-use").addEventListener("click", () => {
    if (listing) apply(listing.path);
  });
  $("folder-system").addEventListener("click", native);
  $("folder-picker").addEventListener("close", () => {
    generation++;
    target = null;
    listing = null;
  });
  $("folder-create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!listing) return;
    const token = generation;
    $("folder-create").disabled = true;
    try {
      const result = await api("/local/folders", {
        method: "POST",
        body: JSON.stringify({
          parent: listing.path,
          name: $("folder-name").value.trim(),
        }),
      });
      if (token !== generation || !$("folder-picker").open) return;
      $("folder-name").value = "";
      await load(result.path);
      toast("Folder created.");
    } catch (error) {
      if (token === generation) fail(error);
    } finally {
      $("folder-create").disabled = false;
    }
  });
  return { open };
}

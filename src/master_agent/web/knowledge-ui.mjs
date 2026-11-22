import { setText, msg, number } from "./i18n.mjs?v=0.5.0";
export function mountKnowledge({ api, state, el, showError, toast }) {
  const $ = (id) => document.getElementById(id);
  let refreshing = false;
  async function refresh() {
    if (!state.key) {
      $("docs-sync").disabled = true;
      setText(
        $("docs-status"),
        "Connect your workspace to manage documentation.",
      );
      return;
    }
    const data = await api("/knowledge/status");
    $("docs-sync").disabled = data.enabled === false || refreshing;
    setText(
      $("docs-status"),
      data.enabled === false
        ? "Documentation retrieval is disabled."
        : msg("{pages} pages · {chunks} passages · local BM25 retrieval", {
            pages: number(data.sources?.length || 0),
            chunks: number(data.passages || 0),
          }),
    );
    const root = $("docs-sources");
    root.replaceChildren();
    for (const source of data.sources || []) {
      const row = el("p", "service-detail");
      const link = el("a", "", `${source.family}: ${source.title}`, false);
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      row.append(link, el("small", "", source.synced_at, false));
      root.append(row);
    }
  }
  $("help-button").addEventListener("click", () => refresh().catch(showError));
  $("docs-sync").addEventListener("click", async () => {
    if (!state.key || refreshing) return;
    refreshing = true;
    $("docs-sync").disabled = true;
    setText($("docs-status"), "Synchronizing official documentation…");
    try {
      const result = await api("/knowledge/sync", { method: "POST" });
      const failed = result.results.filter((r) => r.status === "failed").length;
      toast(
        failed
          ? msg(
              "{count} documentation pages could not be updated. Previous copies were kept.",
              { count: number(failed) },
            )
          : "Official documentation synchronized.",
      );
    } catch (error) {
      showError(error);
    } finally {
      refreshing = false;
      await refresh().catch(showError);
    }
  });
  return { refresh };
}

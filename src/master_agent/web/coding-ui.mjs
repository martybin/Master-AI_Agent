// Owner controls: no page/model text can approve a permission request.
export function mountCoding({
  api,
  state,
  el,
  button,
  showDialog,
  showError,
  toast,
  requireConnection,
  resume,
}) {
  const $ = (id) => document.getElementById(id);
  let currentRequest = null,
    selectedProject = null;
  function fail(error) {
    showError(error);
    $("coding-error").textContent =
      typeof error.detail === "string"
        ? error.detail
        : "Could not complete the operation. Check the project status and access permissions.";
    $("coding-error").hidden = false;
  }
  async function openAccess(record, { pendingOnly = false } = {}) {
    if (!requireConnection()) return;
    try {
      currentRequest = await api(`/local/access-requests/${record.id}`);
      if (pendingOnly && currentRequest.status !== "pending") return;
      $("access-reason").textContent = currentRequest.reason;
      $("access-folder").value = currentRequest.workspace;
      $("access-level").value = currentRequest.mode;
      $("access-confirm").checked = false;
      $("access-error").hidden = true;
      $("access-approve").disabled = currentRequest.status !== "pending";
      $("access-reject").disabled = currentRequest.status !== "pending";
      $("access-resume").hidden = currentRequest.status !== "approved";
      $("access-status").textContent =
        {
          pending: "Waiting for your approval",
          approved: "Access approved",
          rejected: "Access denied",
          superseded: "Superseded by a newer request",
        }[currentRequest.status] || currentRequest.status;
      showDialog("coding-access-dialog");
    } catch (error) {
      showError(error);
    }
  }
  $("access-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!currentRequest) return;
    $("access-approve").disabled = true;
    try {
      const result = await api(
        `/local/access-requests/${currentRequest.id}/approve`,
        {
          method: "POST",
          body: JSON.stringify({
            confirm: $("access-confirm").checked,
            mode: $("access-level").value,
            workspace: $("access-folder").value,
            hours: 4,
          }),
        },
      );
      $("coding-access-dialog").close();
      await resume(result);
    } catch (error) {
      $("access-error").textContent =
        typeof error.detail === "string"
          ? error.detail
          : "Access was not approved. Check the confirmation, folder, and chat status.";
      $("access-error").hidden = false;
    } finally {
      $("access-approve").disabled = false;
    }
  });
  $("access-reject").addEventListener("click", async () => {
    if (!currentRequest) return;
    try {
      await api(`/local/access-requests/${currentRequest.id}/reject`, {
        method: "POST",
      });
      $("coding-access-dialog").close();
      toast("Access request declined.");
    } catch (error) {
      showError(error);
    }
  });
  $("access-resume").addEventListener("click", async () => {
    if (currentRequest) {
      $("coding-access-dialog").close();
      await resume(currentRequest);
    }
  });
  async function download(project) {
    const response = await fetch(
      `/api/v1/local/projects/${project.id}/archive`,
      { headers: { Authorization: `Bearer ${state.key}` } },
    );
    if (!response.ok)
      throw { status: response.status, detail: (await response.json()).detail };
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `biscode-${project.id}.zip`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function openProjects(projectId = null) {
    if (!requireConnection()) return;
    selectedProject = projectId;
    $("coding-changes").replaceChildren();
    $("coding-error").hidden = true;
    showDialog("coding-projects-dialog");
    try {
      const query =
        !projectId && state.chat
          ? "?chat_id=" + encodeURIComponent(state.chat.id)
          : "";
      const data = await api("/local/projects" + query);
      const list = $("coding-project-list");
      list.replaceChildren();
      const projects = projectId
        ? data.projects.filter((p) => p.id === projectId)
        : data.projects;
      if (!projects.length)
        list.append(
          el(
            "p",
            "service-detail",
            "No Python agent projects in this chat yet. Choose Python agent as the build method to create one.",
          ),
        );
      for (const project of projects) {
        const card = el("article", "coding-project");
        card.append(
          el("h3", "", project.name),
          el("p", "coding-path", project.path),
          el(
            "p",
            "service-detail",
            `${project.files.length} files · ${project.status === "created" ? "Files created" : "Incomplete — review required"}`,
          ),
        );
        const run = project.last_run;
        let label = "Tests have not been run";
        if (run)
          label =
            run.status === "passed"
              ? project.checks_current
                ? "Latest check: tests passed"
                : "Previous tests passed; files have changed"
              : run.status === "running"
                ? "Tests running"
                : run.status === "cancelled"
                  ? "Tests cancelled"
                  : "Latest check: tests failed";
        card.append(el("p", "coding-check", label));
        const files = el("details");
        files.append(
          el("summary", "", "Project files"),
          el("pre", "coding-output", project.files.join("\n")),
        );
        card.append(files);
        if (run?.result) {
          const output = el("details");
          output.append(
            el("summary", "", "Latest test output"),
            el(
              "pre",
              "coding-output",
              (run.result.stdout || "") + (run.result.stderr || ""),
            ),
          );
          card.append(output);
        }
        card.append(
          button(
            "Download project ZIP",
            () => download(project).catch(fail),
            "secondary-button",
          ),
          button(
            "View changes",
            () => showChanges(project.chat_id).catch(fail),
            "secondary-button",
          ),
        );
        list.append(card);
      }
    } catch (error) {
      fail(error);
    }
  }
  async function showChanges(chatId = state.chat?.id) {
    if (!chatId) {
      toast("Select a chat first.");
      return;
    }
    const data = await api(
      "/local/code-changes?chat_id=" + encodeURIComponent(chatId),
    );
    const root = $("coding-changes");
    root.replaceChildren(el("h3", "", "Changes in this chat"));
    if (!data.changes.length)
      root.append(
        el("p", "service-detail", "No managed code changes in this chat yet."),
      );
    for (const change of data.changes) {
      const detail = el("details");
      detail.append(
        el("summary", "coding-path", change.path),
        el("pre", "coding-output", change.diff || "No content changes."),
      );
      if (change.truncated)
        detail.append(
          el(
            "p",
            "service-detail",
            "This diff is truncated. Review the full file in the project.",
          ),
        );
      root.append(detail);
    }
  }
  $("coding-projects-button").addEventListener("click", () => openProjects());
  $("coding-refresh").addEventListener("click", () =>
    openProjects(selectedProject),
  );
  $("coding-show-changes").addEventListener("click", () =>
    showChanges().catch(fail),
  );
  return {
    openProjects,
    openAccess,
    renderMessage(message, body) {
      if (message.access_request) {
        body.append(
          el(
            "p",
            "coding-access-notice",
            "Review workspace access here to continue coding. Approval is always yours.",
          ),
        );
        body.append(
          button(
            "Choose folder & approve access",
            () => openAccess(message.access_request),
            "message-build-card",
          ),
        );
      }
      if (message.project_id)
        body.append(
          button(
            "View project, tests & changes",
            () => openProjects(message.project_id),
            "message-build-card",
          ),
        );
    },
  };
}

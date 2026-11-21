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
        : "عملیات کامل نشد؛ وضعیت پروژه و دسترسی را بررسی کن.";
    $("coding-error").hidden = false;
  }
  async function openAccess(record) {
    if (!requireConnection()) return;
    try {
      currentRequest = await api(`/local/access-requests/${record.id}`);
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
          pending: "منتظر اجازهٔ تو",
          approved: "اجازه ثبت شده است",
          rejected: "اجازه داده نشد",
          superseded: "درخواست تازه‌تری وجود دارد",
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
          : "اجازه ثبت نشد؛ تأیید، پوشه و وضعیت گفتگو را بررسی کن.";
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
      toast("درخواست دسترسی رد شد.");
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
            "هنوز پروژهٔ Python در این گفتگو ساخته نشده است. روش ساخت را Python انتخاب کن و درخواستت را بنویس.",
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
            `${project.files.length} فایل · ${project.status === "created" ? "فایل‌ها ساخته شده‌اند" : "ساخت ناقص؛ نیاز به بررسی"}`,
          ),
        );
        const run = project.last_run;
        let label = "تست هنوز اجرا نشده است";
        if (run)
          label =
            run.status === "passed"
              ? project.checks_current
                ? "آخرین بررسی: تست‌ها پاس شدند"
                : "قبولی قبلی؛ فایل‌ها تغییر کرده‌اند"
              : run.status === "running"
                ? "تست در حال اجراست"
                : run.status === "cancelled"
                  ? "تست متوقف شد"
                  : "آخرین بررسی: تست ناموفق";
        card.append(el("p", "coding-check", label));
        const files = el("details");
        files.append(
          el("summary", "", "فایل‌های پروژه"),
          el("pre", "coding-output", project.files.join("\n")),
        );
        card.append(files);
        if (run?.result) {
          const output = el("details");
          output.append(
            el("summary", "", "خروجی واقعی آخرین تست"),
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
            "دریافت پروژه ZIP",
            () => download(project).catch(fail),
            "secondary-button",
          ),
          button(
            "مشاهدهٔ تغییرات",
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
      toast("ابتدا یک گفتگو انتخاب کن.");
      return;
    }
    const data = await api(
      "/local/code-changes?chat_id=" + encodeURIComponent(chatId),
    );
    const root = $("coding-changes");
    root.replaceChildren(el("h3", "", "تغییرات ثبت‌شده در این گفتگو"));
    if (!data.changes.length)
      root.append(
        el(
          "p",
          "service-detail",
          "هنوز تغییری با ابزار ویرایش کد ثبت نشده است.",
        ),
      );
    for (const change of data.changes) {
      const detail = el("details");
      detail.append(
        el("summary", "coding-path", change.path),
        el("pre", "coding-output", change.diff || "تغییری در محتوا نیست."),
      );
      if (change.truncated)
        detail.append(
          el(
            "p",
            "service-detail",
            "نمایش تغییرات کوتاه شده است. فایل اصلی را در پروژه بررسی کن.",
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
    renderMessage(message, body) {
      if (message.access_request)
        body.append(
          button(
            "بررسی درخواست دسترسی و ادامه",
            () => openAccess(message.access_request),
            "message-build-card",
          ),
        );
      if (message.project_id)
        body.append(
          button(
            "مشاهدهٔ پروژه، تست‌ها و تغییرات",
            () => openProjects(message.project_id),
            "message-build-card",
          ),
        );
    },
  };
}

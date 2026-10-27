(() => {
  function container() {
    let root = document.querySelector(".notifications");
    if (!root) { root = document.createElement("div"); root.className = "notifications"; root.setAttribute("aria-label", "Notificaciones"); document.body.append(root); }
    return root;
  }
  function prepare(toast, kind) {
    toast.dataset.kind = kind;
    toast.setAttribute("role", kind === "error" ? "alert" : "status");
    const close = toast.querySelector(".toast-close");
    close?.addEventListener("click", () => toast.remove());
    if (kind === "error" || kind === "warning") return;
    let timer;
    const pause = () => clearTimeout(timer);
    const resume = () => { pause(); timer = setTimeout(() => { if (!toast.matches(":hover") && !toast.contains(document.activeElement)) toast.remove(); }, 8000); };
    toast.addEventListener("mouseenter", pause);
    toast.addEventListener("mouseleave", resume);
    toast.addEventListener("focusin", pause);
    toast.addEventListener("focusout", resume);
    resume();
  }
  const titles = { success: "Completado", error: "Error", warning: "Atención", info: "Información" };
  window.createToast = (type, _icon, title, text) => {
    const kind = Object.hasOwn(titles, type) ? type : "info";
    const toast = document.createElement("div"); toast.className = "toast";
    const copy = document.createElement("div"); copy.className = "toast-copy";
    const heading = document.createElement("strong"); heading.textContent = title || titles[kind];
    const body = document.createElement("p"); body.textContent = String(text ?? "");
    const close = document.createElement("button"); close.type = "button"; close.className = "toast-close"; close.setAttribute("aria-label", "Cerrar notificación"); close.textContent = "✕";
    copy.append(heading, body); toast.append(copy, close); prepare(toast, kind); container().append(toast);
  };
  for (const kind of Object.keys(titles)) window[kind] = (text) => window.createToast(kind, null, titles[kind], text);
  document.querySelectorAll("[data-toast-type]").forEach((toast) => {
    const tags = toast.dataset.toastType.split(/\s+/);
    prepare(toast, ["error", "success", "warning", "info"].find((kind) => tags.includes(kind)) || "info");
  });
})();

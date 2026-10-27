/* Interacción compartida: sin datos persistentes ni política de permisos. */
(() => {
  const openers = new WeakMap();
  const closingTimers = new WeakMap();
  let scrollBeforeDialog = "";
  function openDialog(dialog, trigger = document.activeElement) {
    if (!dialog || dialog.open) return;
    openers.set(dialog, trigger);
    scrollBeforeDialog = document.body.style.overflow;
    dialog.showModal();
    document.body.style.overflow = "hidden";
    trigger?.setAttribute("aria-expanded", "true");
  }
  function closeDialog(dialog) {
    if (!dialog?.open || dialog.classList.contains("is-closing")) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) { dialog.close(); return; }
    dialog.classList.add("is-closing");
    const duration = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--motion-panel")) || 220;
    closingTimers.set(dialog, setTimeout(() => { dialog.classList.remove("is-closing"); dialog.close(); }, duration));
  }
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-open-dialog]");
    if (trigger) openDialog(document.getElementById(trigger.dataset.openDialog), trigger);
    const closer = event.target.closest("[data-close-dialog]");
    if (closer) closeDialog(closer.closest("dialog"));
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); closeDialog(dialog); });
    dialog.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      const controls = [...dialog.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]')]
        .filter((node) => node.tabIndex >= 0 && node.getClientRects().length);
      const first = controls[0], last = controls.at(-1);
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) closeDialog(dialog);
    });
    dialog.addEventListener("close", () => {
      clearTimeout(closingTimers.get(dialog));
      dialog.classList.remove("is-closing");
      document.body.style.overflow = scrollBeforeDialog;
      const trigger = openers.get(dialog);
      trigger?.setAttribute("aria-expanded", "false");
      if (trigger?.isConnected && trigger.getClientRects().length) trigger.focus();
    });
  });
  const desktop = matchMedia("(min-width: 1024px)");
  desktop.addEventListener("change", (event) => {
    if (!event.matches) return;
    const dialog = document.getElementById("mobile-navigation");
    if (dialog?.open) { dialog.classList.remove("is-closing"); dialog.close(); document.querySelector(".desktop-sidebar a")?.focus(); }
  });
  document.querySelectorAll("[data-account-menu]").forEach((menu) => {
    const toggle = menu.querySelector("[data-account-toggle]");
    const panel = menu.querySelector("[data-account-panel]");
    const close = (restore = false) => { panel.hidden = true; toggle.setAttribute("aria-expanded", "false"); if (restore) toggle.focus(); };
    toggle.addEventListener("click", () => { panel.hidden = !panel.hidden; toggle.setAttribute("aria-expanded", String(!panel.hidden)); });
    menu.addEventListener("keydown", (event) => { if (event.key === "Escape" && !panel.hidden) { event.stopPropagation(); close(true); } });
    menu.addEventListener("focusout", (event) => { if (!menu.contains(event.relatedTarget)) close(); });
    document.addEventListener("click", (event) => { if (!menu.contains(event.target)) close(); });
  });
  window.baseDialogs = { open: openDialog, close: closeDialog };
  document.querySelectorAll("form .errorlist").forEach((node) => node.setAttribute("role", "alert"));
  document.querySelector("form [aria-invalid='true']")?.focus();
})();

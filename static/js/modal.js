async function abrir_modal(url, formId = null, modalId = "containerModal", bodyId = "modalBody") {
  const dialog = document.getElementById(modalId);
  const body = document.getElementById(bodyId);
  if (!dialog || !body) return;
  try {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw new Error("Respuesta inválida");
    const content = await response.text();
    if (!window.DOMPurify) throw new Error("Contenido no disponible");
    body.innerHTML = DOMPurify.sanitize(content);
    if (formId && typeof initializeForm === "function") initializeForm(formId);
    window.baseDialogs.open(dialog);
  } catch (_error) { window.error?.("No se pudo abrir el contenido. Vuelve a intentarlo."); }
}

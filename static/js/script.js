(() => {
  const input = document.getElementById("id_image");
  const button = document.getElementById("customImageButton");
  const name = document.getElementById("nameImg");
  if (!input || !button || !name) return;
  button.addEventListener("click", () => input.click());
  input.addEventListener("change", () => { name.textContent = input.files[0] ? "Nueva imagen: " + input.files[0].name : ""; });
})();

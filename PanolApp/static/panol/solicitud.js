(() => {
  const script = document.currentScript;
  const docente = document.getElementById('id_docencia');
  if (!docente) return;
  const carrera = document.getElementById('id_carrera');
  const asignatura = document.getElementById('id_asignatura');
  const tipo = document.getElementById('id_tipo');
  function actualizar() {
    for (const option of docente.options) {
      if (!option.value) continue;
      const disponible = (!carrera.value || option.dataset.carrera === carrera.value) &&
        (!asignatura.value || option.dataset.asignatura === asignatura.value) &&
        (tipo.value !== 'docente' || option.dataset.docente === script.dataset.actor);
      option.hidden = !disponible;
      option.disabled = !disponible;
      if (!disponible && option.selected) docente.value = '';
    }
  }
  [carrera, asignatura, tipo].forEach(c => c.addEventListener('change', actualizar));
  actualizar();
})();

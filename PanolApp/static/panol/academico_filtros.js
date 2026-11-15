(() => {
  const formulario = document.querySelector('[data-academico-filtros]');
  const estado = document.querySelector('[data-estado-filtros]');
  const boton = formulario.querySelector('button[type="submit"]');
  const contexto = ['periodo', 'carrera', 'asignatura'];
  let pendiente;

  async function actualizar() {
    pendiente?.abort();
    const consulta = new AbortController();
    pendiente = consulta;
    const url = new URL(formulario.action);
    const parametros = new URLSearchParams({ opciones: '1' });
    for (const nombre of contexto) {
      parametros.set(nombre, formulario.elements.namedItem(nombre).value);
    }
    url.search = parametros.toString();
    boton.disabled = true;
    estado.textContent = 'Actualizando opciones…';
    const espera = window.setTimeout(() => consulta.abort(), 5000);

    try {
      const respuesta = await fetch(url, { signal: consulta.signal, redirect: 'error' });
      if (!respuesta.ok) throw new Error('No se pudieron cargar las opciones');
      const opciones = await respuesta.json();
      if (pendiente !== consulta) return;

      for (const nombre of ['carrera', 'asignatura', 'docente']) {
        const campo = formulario.elements.namedItem(nombre);
        const anterior = campo.value;
        campo.replaceChildren(...opciones[nombre].map(([valor, etiqueta]) => new Option(etiqueta, valor)));
        campo.value = opciones[nombre].some(([valor]) => valor === anterior) ? anterior : '';
      }
      estado.textContent = '';
    } catch {
      if (pendiente === consulta) {
        estado.textContent = 'No se pudieron actualizar las opciones. Pulsa Filtrar para cargarlas.';
      }
    } finally {
      window.clearTimeout(espera);
      if (pendiente === consulta) {
        pendiente = undefined;
        boton.disabled = false;
      }
    }
  }

  for (const nombre of contexto) {
    formulario.elements.namedItem(nombre).addEventListener('change', actualizar);
  }
})();

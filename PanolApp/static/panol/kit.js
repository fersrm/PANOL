(() => {
  const button = document.getElementById('agregar-componente');
  if (!button) return;
  button.addEventListener('click', () => {
    const total = document.querySelector('input[name="componentes-TOTAL_FORMS"]');
    const index = Number(total.value);
    if (index >= 100) return;
    const fragment = document.getElementById('kit-vacio').content.cloneNode(true);
    fragment.querySelectorAll('[name], [id], [for]').forEach(element => {
      ['name', 'id', 'for'].forEach(attr => {
        if (element.hasAttribute(attr)) element.setAttribute(attr, element.getAttribute(attr).replaceAll('__prefix__', String(index)));
      });
    });
    document.getElementById('kit-componentes').append(fragment);
    total.value = index + 1;
    document.getElementById(`id_componentes-${index}-articulo`)?.focus();
  });
})();

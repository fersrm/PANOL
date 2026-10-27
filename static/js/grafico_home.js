(() => {
  const element = document.getElementById("chart_home");
  const source = document.getElementById("panol-grafico");
  if (!element || !source) return;
  const datos = JSON.parse(source.textContent);
  if (!window.echarts) { element.hidden = true; const fallback = document.getElementById("chart-unavailable"); if (fallback) fallback.hidden = false; return; }
  const styles = getComputedStyle(document.documentElement);
  // Resolver también las referencias var() mediante una propiedad computada real.
  function color(name) {
    const probe = document.createElement("span"); probe.style.color = `var(${name})`; document.body.append(probe);
    const value = getComputedStyle(probe).color; probe.remove(); return value;
  }
  const ink = color("--ink"), muted = color("--muted"), line = color("--border");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const chart = echarts.init(element);
  const options = {
    animation: !reduced.matches,
    color: [color("--chart-1"), color("--chart-2"), color("--chart-3")],
    textStyle: { fontFamily: styles.getPropertyValue("--font-ui").trim(), color: ink },
    tooltip: { trigger: "axis", backgroundColor: color("--surface"), borderColor: line, textStyle: { color: ink }, confine: true },
    legend: { data: ["Entregas", "Recepciones"], textStyle: { color: muted }, top: 14 },
    toolbox: { show: true, right: 16, top: 44, feature: { magicType: { show: true, type: ["line", "bar"], title: { line: "Ver líneas", bar: "Ver barras" } }, restore: { show: true, title: "Restablecer" }, saveAsImage: { show: true, title: "Guardar imagen" } }, iconStyle: { borderColor: muted } },
    grid: { left: 46, right: 46, bottom: 32, top: 98, containLabel: true },
    xAxis: { type: "category", data: datos.dias, axisLabel: { color: muted }, axisLine: { lineStyle: { color: line } }, axisTick: { show: false } },
    yAxis: { type: "value", min: 0, minInterval: 1, axisLabel: { color: muted }, splitLine: { lineStyle: { color: line } } },
    series: [{ name: "Entregas", type: "bar", data: datos.entregas, itemStyle: { borderRadius: [3,3,0,0] } }, { name: "Recepciones", type: "line", data: datos.devoluciones }]
  };
  chart.setOption(options);
  const observer = new ResizeObserver(() => chart.resize()); observer.observe(element);
  reduced.addEventListener("change", (event) => chart.setOption({ animation: !event.matches }));
  window.addEventListener("pagehide", (event) => { if (!event.persisted) { observer.disconnect(); chart.dispose(); } });
})();

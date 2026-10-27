(() => {
  const element = document.getElementById("chart_home");
  if (!element) return;
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
    legend: { data: ["Evaporación", "Precipitación", "Temperatura"], textStyle: { color: muted }, top: 14 },
    toolbox: { show: true, right: 16, top: 44, feature: { magicType: { show: true, type: ["line", "bar"], title: { line: "Ver líneas", bar: "Ver barras" } }, restore: { show: true, title: "Restablecer" }, saveAsImage: { show: true, title: "Guardar imagen" } }, iconStyle: { borderColor: muted } },
    grid: { left: 46, right: 46, bottom: 32, top: 98, containLabel: true },
    xAxis: { type: "category", data: ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"], axisLabel: { color: muted }, axisLine: { lineStyle: { color: line } }, axisTick: { show: false } },
    yAxis: [{ type: "value", name: "ml", min: 0, max: 250, interval: 50, axisLabel: { color: muted }, nameTextStyle: { color: muted }, splitLine: { lineStyle: { color: line } } }, { type: "value", name: "°C", min: 0, max: 25, interval: 5, axisLabel: { color: muted }, nameTextStyle: { color: muted }, splitLine: { show: false } }],
    series: [{ name: "Evaporación", type: "bar", data: [2,4.9,7,23.2,25.6,76.7,135.6,162.2,32.6,20,6.4,3.3], itemStyle: { borderRadius: [3,3,0,0] } }, { name: "Precipitación", type: "bar", data: [2.6,5.9,9,26.4,28.7,70.7,175.6,182.2,48.7,18.8,6,2.3], itemStyle: { borderRadius: [3,3,0,0] } }, { name: "Temperatura", type: "line", yAxisIndex: 1, data: [2,2.2,3.3,4.5,6.3,10.2,20.3,23.4,23,16.5,12,6.2] }]
  };
  chart.setOption(options);
  const observer = new ResizeObserver(() => chart.resize()); observer.observe(element);
  reduced.addEventListener("change", (event) => chart.setOption({ animation: !event.matches }));
  window.addEventListener("pagehide", (event) => { if (!event.persisted) { observer.disconnect(); chart.dispose(); } });
})();

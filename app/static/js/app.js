/* EV Fleet Monitor shared front-end: theme switching, sidebar, chart theme, map tiles. */
(() => {
  const root = document.documentElement;
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };
  const css = (name) => getComputedStyle(root).getPropertyValue(name).trim();
  const themeListeners = [];

  // ---- theme ---------------------------------------------------------------------------
  function setTheme(theme) {
    root.dataset.bsTheme = theme;
    store.set("ev-theme", theme);
    themeListeners.forEach((fn) => fn(theme));
  }
  document.querySelectorAll("[data-theme-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => setTheme(root.dataset.bsTheme === "dark" ? "light" : "dark")),
  );

  // ---- sidebar collapse (desktop) --------------------------------------------------------
  document.querySelectorAll("[data-sidebar-collapse]").forEach((btn) =>
    btn.addEventListener("click", () => {
      root.classList.toggle("sidebar-collapsed");
      store.set("ev-sidebar", root.classList.contains("sidebar-collapsed") ? "collapsed" : "open");
      window.dispatchEvent(new Event("resize")); // let maps/charts re-measure
    }),
  );

  // ---- charts (Chart.js) ---------------------------------------------------------------
  function styleCharts() {
    if (!window.Chart) return;
    const text = css("--ev-muted");
    const grid = css("--ev-border");
    Chart.defaults.font.family = css("--ev-font");
    Chart.defaults.color = text;
    Chart.defaults.borderColor = grid;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.boxWidth = 8;
    Chart.defaults.plugins.tooltip.backgroundColor = css("--ev-surface-2") || "#111827";
    Chart.defaults.plugins.tooltip.titleColor = css("--ev-text");
    Chart.defaults.plugins.tooltip.bodyColor = css("--ev-text");
    Chart.defaults.plugins.tooltip.borderColor = grid;
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Object.values(Chart.instances || {}).forEach((chart) => {
      Object.values(chart.options.scales || {}).forEach((scale) => {
        scale.ticks = { ...(scale.ticks || {}), color: text };
        scale.grid = { ...(scale.grid || {}), color: grid };
        if (scale.title) scale.title.color = text;
      });
      chart.update("none");
    });
  }
  // Pages load Chart.js after this file, so apply defaults once everything has loaded.
  window.addEventListener("DOMContentLoaded", styleCharts);
  themeListeners.push(() => styleCharts());

  // ---- map tiles (Leaflet) -------------------------------------------------------------
  // OpenStreetMap tiles (no API key). In dark mode a CSS filter inverts only the tile layer
  // (see .leaflet-tile-pane in app.css), so vehicle markers keep their colours.
  // Usage: EV.tileLayer(map)
  function tileLayer(map) {
    return L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
  }

  window.EV = { tileLayer, onThemeChange: (fn) => themeListeners.push(fn), css };
})();

const rows = document.querySelector("#service-rows");
const summary = document.querySelector("#summary");
const headerState = document.querySelector("#header-state");
const logPanel = document.querySelector("#log-panel");
const logTitle = document.querySelector("#log-title");
const logOutput = document.querySelector("#log-output");
const toast = document.querySelector("#toast");

let services = [];
let busy = false;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function notify(message, isError = false) {
  toast.textContent = message;
  toast.className = `toast visible${isError ? " error" : ""}`;
  window.clearTimeout(notify.timer);
  notify.timer = window.setTimeout(() => {
    toast.className = "toast";
  }, 3200);
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Request failed");
  return data;
}

function renderSummary() {
  const counts = {
    total: services.length,
    running: services.filter((item) => item.state === "running").length,
    stopped: services.filter((item) => item.state === "stopped").length,
    external: services.filter((item) => item.state === "external").length,
    healthy: services.filter((item) => item.healthy === true).length,
    unhealthy: services.filter((item) => item.healthy === false).length,
  };
  const metrics = [
    ["Total services", counts.total, ""],
    ["Running", counts.running, "healthy"],
    ["Stopped", counts.stopped, counts.stopped ? "danger" : ""],
    ["External", counts.external, counts.external ? "warning" : ""],
    ["Healthy", counts.healthy, "healthy"],
    ["Unhealthy", counts.unhealthy, counts.unhealthy ? "danger" : ""],
  ];
  summary.innerHTML = metrics.map(([label, value, cls]) => `
    <div class="metric">
      <span class="metric-label">${label}</span>
      <span class="metric-value ${cls}">${value}</span>
    </div>
  `).join("");
  headerState.textContent = `${counts.running} running · ${counts.external} external · ${counts.stopped} stopped`;
}

function actionButton(item, label, action, disabled = false, extra = "") {
  return `<button class="button button-small ${extra}" data-service="${escapeHtml(item.name)}" data-action="${action}" ${disabled ? "disabled" : ""}>${label}</button>`;
}

function renderRows() {
  if (!services.length) {
    rows.innerHTML = `<tr><td colspan="7" class="empty">No services configured.</td></tr>`;
    return;
  }
  rows.innerHTML = services.map((item) => {
    const running = item.state === "running";
    const external = item.state === "external";
    const stopped = item.state === "stopped";
    const openDisabled = !item.web_url;
    const health = item.healthy === true
      ? `<span class="health health-good">Healthy</span>`
      : item.healthy === false
        ? `<span class="health">Unhealthy</span>`
        : `<span class="health">—</span>`;
    return `
      <tr>
        <td class="service-name">${escapeHtml(item.name)}</td>
        <td class="description">${escapeHtml(item.description)}</td>
        <td>${escapeHtml(item.type)}</td>
        <td class="mono">${item.port ?? "—"}</td>
        <td><span class="state state-${item.state}">${item.state[0].toUpperCase() + item.state.slice(1)}</span></td>
        <td>${health}</td>
        <td>
          <div class="actions">
            ${item.web_url
              ? `<a class="button button-small" href="${escapeHtml(item.web_url)}" target="_blank" rel="noreferrer">Open</a>`
              : `<button class="button button-small" disabled>Open</button>`}
            ${actionButton(item, "Start", "start", !stopped)}
            ${actionButton(item, "Stop", "stop", !running || external, "button-danger")}
            ${actionButton(item, "Restart", "restart", !running)}
            ${actionButton(item, "Logs", "logs")}
          </div>
        </td>
      </tr>
    `;
  }).join("");
}

async function loadServices(silent = false) {
  if (busy) return;
  busy = true;
  if (!silent) headerState.textContent = "Refreshing…";
  try {
    const data = await request("/api/services");
    services = data.services;
    renderSummary();
    renderRows();
  } catch (error) {
    rows.innerHTML = `<tr><td colspan="7" class="empty">Could not load services.</td></tr>`;
    notify(error.message, true);
  } finally {
    busy = false;
  }
}

async function runAction(name, action) {
  try {
    notify(`${action} ${name}…`);
    await request(`/api/services/${encodeURIComponent(name)}/action`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    await loadServices(true);
    notify(`${name}: ${action} complete`);
  } catch (error) {
    notify(`${name}: ${error.message}`, true);
    await loadServices(true);
  }
}

async function showLogs(name) {
  try {
    const data = await request(`/api/services/${encodeURIComponent(name)}/logs?lines=160`);
    logTitle.textContent = name;
    logOutput.textContent = data.lines.length ? data.lines.join("\n") : "No log lines.";
    logPanel.hidden = false;
    logPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    notify(error.message, true);
  }
}

rows.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const { service, action } = button.dataset;
  if (action === "logs") showLogs(service);
  else runAction(service, action);
});

document.querySelector(".toolbar").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-bulk]");
  if (!button) return;
  const action = button.dataset.bulk;
  if (action === "refresh") return loadServices();
  try {
    notify(`${action === "start-web" ? "Starting web services" : "Stopping managed services"}…`);
    const data = await request(`/api/actions/${action}`, { method: "POST" });
    await loadServices(true);
    const failures = data.results.filter((item) => item.error).length;
    notify(failures ? `${failures} action(s) failed` : "Bulk action complete", Boolean(failures));
  } catch (error) {
    notify(error.message, true);
  }
});

document.querySelector("#close-logs").addEventListener("click", () => {
  logPanel.hidden = true;
});

loadServices();
window.setInterval(() => loadServices(true), 15000);

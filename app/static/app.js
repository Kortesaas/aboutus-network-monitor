const REFRESH_INTERVAL_MS = 15000;

const elements = {
  stationName: document.querySelector("#stationName"),
  lastUpdated: document.querySelector("#lastUpdated"),
  refreshButton: document.querySelector("#refreshButton"),
  summaryGrid: document.querySelector("#summaryGrid"),
  infrastructureGrid: document.querySelector("#infrastructureGrid"),
  internetGrid: document.querySelector("#internetGrid"),
  vlanTable: document.querySelector("#vlanTable"),
  deviceTable: document.querySelector("#deviceTable"),
};

function text(value) {
  if (value === null || value === undefined || value === "") {
    return "Unknown";
  }
  return String(value);
}

function statusClass(status) {
  const normalized = text(status).toLowerCase();
  if (normalized === "online" || normalized === "offline") {
    return normalized;
  }
  return "unknown";
}

function statusLabel(status) {
  return statusClass(status).toUpperCase();
}

function formatTime(value) {
  if (!value || value === "Unknown") {
    return "Unknown";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return text(value);
  }

  return date.toLocaleString();
}

function formatLatency(check) {
  if (!check || check.latency_ms === null || check.latency_ms === undefined) {
    return "Unknown";
  }
  return `${check.latency_ms} ms`;
}

function clearNode(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function createStatusPill(status) {
  const pill = document.createElement("span");
  pill.className = `pill ${statusClass(status)}`;
  pill.textContent = statusLabel(status);
  return pill;
}

function createCard(title, status, details = [], options = {}) {
  const card = document.createElement("article");
  card.className = `card${options.compact ? " compact" : ""}`;

  const head = document.createElement("div");
  head.className = "card-head";

  const heading = document.createElement("h3");
  heading.textContent = title;
  head.appendChild(heading);
  head.appendChild(createStatusPill(status));
  card.appendChild(head);

  if (options.meta) {
    const meta = document.createElement("p");
    meta.className = "meta";
    meta.textContent = options.meta;
    card.appendChild(meta);
  }

  if (details.length > 0) {
    const list = document.createElement("dl");
    list.className = "detail-list";

    details.forEach(([label, value]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const description = document.createElement("dd");
      description.textContent = text(value);
      list.appendChild(term);
      list.appendChild(description);
    });

    card.appendChild(list);
  }

  return card;
}

function createCell(value) {
  const cell = document.createElement("td");
  cell.textContent = text(value);
  return cell;
}

function createStatusCell(status) {
  const cell = document.createElement("td");
  cell.appendChild(createStatusPill(status));
  return cell;
}

function renderSummary(data) {
  clearNode(elements.summaryGrid);

  const infrastructure = data.infrastructure || [];
  const vlans = data.vlans || [];
  const findById = (id) => infrastructure.find((item) => item.id === id);
  const gatewayOnlineCount = vlans.filter((vlan) => vlan.gateway_status === "online").length;

  const cards = [
    ["Router", findById("lancom-router")?.status || "unknown", findById("lancom-router")?.ip_address],
    ["FOH Switch", findById("foh-switch")?.status || "unknown", findById("foh-switch")?.ip_address],
    ["Stage Switch", findById("stage-switch")?.status || "unknown", findById("stage-switch")?.ip_address],
    ["Internet", data.internet?.status || "unknown", `${(data.internet?.probes || []).length} probes`],
    ["VLAN Gateways", gatewayOnlineCount === vlans.length && vlans.length > 0 ? "online" : gatewayOnlineCount > 0 ? "unknown" : "offline", `${gatewayOnlineCount}/${vlans.length} online`],
  ];

  cards.forEach(([title, status, meta]) => {
    elements.summaryGrid.appendChild(createCard(title, status, [], { meta, compact: true }));
  });
}

function renderInfrastructure(items) {
  clearNode(elements.infrastructureGrid);

  if (!items || items.length === 0) {
    elements.infrastructureGrid.appendChild(emptyState("No infrastructure configured."));
    return;
  }

  items.forEach((item) => {
    elements.infrastructureGrid.appendChild(
      createCard(item.name, item.status, [
        ["Role", item.role],
        ["Model", item.model],
        ["IP", item.ip_address],
        ["VLAN", item.vlan],
        ["Latency", formatLatency(item.check)],
        ["Checked", formatTime(item.check?.checked_at)],
      ]),
    );
  });
}

function renderInternet(internet) {
  clearNode(elements.internetGrid);

  const probes = internet?.probes || [];
  if (probes.length === 0) {
    elements.internetGrid.appendChild(emptyState("No internet probes configured."));
    return;
  }

  elements.internetGrid.appendChild(
    createCard("Internet", internet.status, [
      ["Probes", String(probes.length)],
      ["Online", String(probes.filter((probe) => probe.status === "online").length)],
    ], { compact: true }),
  );

  probes.forEach((probe) => {
    elements.internetGrid.appendChild(
      createCard(probe.name, probe.status, [
        ["Target", probe.check?.target],
        ["Latency", formatLatency(probe.check)],
        ["Checked", formatTime(probe.check?.checked_at)],
      ], { compact: true }),
    );
  });
}

function renderVlans(vlans) {
  clearNode(elements.vlanTable);

  (vlans || []).forEach((vlan) => {
    const row = document.createElement("tr");
    row.appendChild(createCell(`${vlan.name} (${vlan.id})`));
    row.appendChild(createCell(vlan.subnet));
    row.appendChild(createCell(vlan.gateway));
    row.appendChild(createStatusCell(vlan.gateway_status));
    row.appendChild(createCell(formatLatency(vlan.gateway_check)));
    row.appendChild(createCell(formatTime(vlan.gateway_check?.checked_at)));
    elements.vlanTable.appendChild(row);
  });
}

function renderDevices(devices) {
  clearNode(elements.deviceTable);

  (devices || []).forEach((device) => {
    const row = document.createElement("tr");
    row.appendChild(createCell(device.display_name));
    row.appendChild(createCell(device.vlan));
    row.appendChild(createCell(device.ip_address));
    row.appendChild(createCell(device.mac_address));
    row.appendChild(createCell(device.hostname));
    row.appendChild(createCell(device.vendor));
    row.appendChild(createStatusCell(device.status));
    row.appendChild(createCell(formatTime(device.last_seen)));
    row.appendChild(createCell((device.discovery_sources || []).join(", ")));
    row.appendChild(createCell(device.connection?.switch_name));
    row.appendChild(createCell(device.connection?.switch_port));
    row.appendChild(createCell(device.connection?.port_state));
    elements.deviceTable.appendChild(row);
  });
}

function emptyState(message) {
  const node = document.createElement("div");
  node.className = "empty";
  node.textContent = message;
  return node;
}

function renderError(message) {
  clearNode(elements.summaryGrid);
  const node = emptyState(message);
  node.classList.add("error");
  elements.summaryGrid.appendChild(node);
}

function render(data) {
  const station = data.station || {};
  elements.stationName.textContent = station.name || "ABOUTUS Network Monitor";
  elements.lastUpdated.textContent = `Updated ${formatTime(data.generated_at)}`;

  if (data.error) {
    renderError(data.error);
  } else {
    renderSummary(data);
  }

  renderInfrastructure(data.infrastructure);
  renderInternet(data.internet);
  renderVlans(data.vlans);
  renderDevices(data.devices);
}

async function loadStatus() {
  elements.refreshButton.disabled = true;
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Status request failed: HTTP ${response.status}`);
    }
    const data = await response.json();
    render(data);
  } catch (error) {
    elements.lastUpdated.textContent = "Status request failed";
    renderError(error.message || "Status request failed.");
  } finally {
    elements.refreshButton.disabled = false;
  }
}

elements.refreshButton.addEventListener("click", loadStatus);
loadStatus();
window.setInterval(loadStatus, REFRESH_INTERVAL_MS);

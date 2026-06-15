const APP_VERSION = "0.4.0";
const SETTINGS_KEY = "aboutus-network-monitor-ui-settings-v3";
const COLLAPSE_KEY = "aboutus-network-monitor-collapsed-vlans-v3";
const OPEN_DEVICES_KEY = "aboutus-network-monitor-open-devices-v4";
const FILTERS_KEY = "aboutus-network-monitor-device-filters-v4";
const ROUTE_KEY = "aboutus-network-monitor-active-route-v4";

const DEFAULT_SETTINGS = {
  theme: "dark",
  refreshInterval: 30000,
  compactMode: false,
  showUnknownDevices: true,
  showOfflineDevices: true,
};

const PAGE_META = {
  overview: {
    kicker: "Overview",
    title: "Live Production Network",
    subtitle: "Show-ready status, critical checks, VLAN health, and active warnings.",
  },
  devices: {
    kicker: "Devices",
    title: "Known and Discovered Devices",
    subtitle: "Grouped by VLAN with search, filters, and expandable technical details.",
  },
  topology: {
    kicker: "Topology",
    title: "Network Path",
    subtitle: "Clean view of the WAN, router, switches, and VLAN lanes.",
  },
  settings: {
    kicker: "Settings",
    title: "Dashboard Preferences",
    subtitle: "Theme, refresh rate, compact mode, and discovery display controls.",
  },
};

const elements = {
  navItems: Array.from(document.querySelectorAll(".nav-item")),
  navIcons: Array.from(document.querySelectorAll("[data-icon]")),
  pageKicker: document.querySelector("#pageKicker"),
  pageTitle: document.querySelector("#pageTitle"),
  pageSubtitle: document.querySelector("#pageSubtitle"),
  pageContent: document.querySelector("#pageContent"),
  stationBadge: document.querySelector("#stationBadge"),
  lastUpdated: document.querySelector("#lastUpdated"),
  refreshButton: document.querySelector("#refreshButton"),
  settingsButton: document.querySelector("#settingsButton"),
};

const state = {
  route: "overview",
  status: null,
  error: null,
  loading: false,
  refreshTimer: null,
  settings: loadSettings(),
  collapsedVlans: loadCollapsedVlans(),
  openDevices: loadOpenDevices(),
  filters: loadFilters(),
};

function icon(name) {
  const icons = {
    dashboard: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 13h6V4H4v9Zm10 7h6V4h-6v16ZM4 20h6v-5H4v5Z"/></svg>',
    devices: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H4V5Zm2 2v7h12V7H6Zm3 12h6v2H9v-2Zm-4 0h14v2H5v-2Z"/></svg>',
    topology: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 3h2v4h-2V3Zm0 14h2v4h-2v-4ZM5 9h14v6H5V9Zm2 2v2h10v-2H7ZM3 11H1V7h6v2H3v2Zm20 0h-2V9h-4V7h6v4Z"/></svg>',
    settings: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.4 13.5c.1-.5.1-1 .1-1.5s0-1-.1-1.5l2-1.5-2-3.5-2.4 1a7.5 7.5 0 0 0-2.6-1.5L14 2h-4l-.4 2.5A7.5 7.5 0 0 0 7 6L4.6 5l-2 3.5 2 1.5c-.1.5-.1 1-.1 1.5s0 1 .1 1.5l-2 1.5 2 3.5 2.4-1a7.5 7.5 0 0 0 2.6 1.5L10 22h4l.4-2.5A7.5 7.5 0 0 0 17 18l2.4 1 2-3.5-2-1.5ZM12 15.5A3.5 3.5 0 1 1 12 8a3.5 3.5 0 0 1 0 7.5Z"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17.7 6.3A8 8 0 1 0 20 12h-2a6 6 0 1 1-1.8-4.3L13 11h8V3l-3.3 3.3Z"/></svg>',
    external: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3h7v7h-2V6.4l-8.3 8.3-1.4-1.4L17.6 5H14V3ZM5 5h7v2H7v10h10v-5h2v7H5V5Z"/></svg>',
    arrow: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 5.6 19.4 12 13 18.4 11.6 17l4-4H4v-2h11.6l-4-4L13 5.6Z"/></svg>',
    internet: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm6.9 9h-3.1a15 15 0 0 0-1.1-5 8.1 8.1 0 0 1 4.2 5ZM12 4.1c.7 1 1.4 3.3 1.7 6.9h-3.4c.3-3.6 1-5.9 1.7-6.9ZM4.3 13h3.9c.1 1.7.4 3.3.8 4.7A8.1 8.1 0 0 1 4.3 13Zm3.9-2H4.3A8.1 8.1 0 0 1 9 6.3 18 18 0 0 0 8.2 11Zm3.8 8.9c-.7-1-1.4-3.3-1.7-6.9h3.4c-.3 3.6-1 5.9-1.7 6.9Zm3-2.2c.4-1.4.7-3 .8-4.7h3.9a8.1 8.1 0 0 1-4.7 4.7Z"/></svg>',
    router: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 11h16v8H4v-8Zm2 2v4h12v-4H6Zm1 3h2v-2H7v2Zm4 0h2v-2h-2v2Zm7-9 1.4-1.4A10.5 10.5 0 0 0 12 2a10.5 10.5 0 0 0-7.4 3.1L6 6.5A8.5 8.5 0 0 1 12 4c2.3 0 4.4.9 6 3Zm-3 3 1.4-1.4A6.2 6.2 0 0 0 12 6.8c-1.7 0-3.2.7-4.4 1.8L9 10a4.3 4.3 0 0 1 6 0Z"/></svg>',
    switch: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7h18v10H3V7Zm2 2v6h14V9H5Zm1 5h2v-2H6v2Zm3 0h2v-2H9v2Zm3 0h2v-2h-2v2Zm3 0h2v-2h-2v2ZM7 4h10v2H7V4Zm0 14h10v2H7v-2Z"/></svg>',
    command: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3a4 4 0 0 0-4 4v1h5V7a4 4 0 0 0-1-2.6V3Zm2 7H3v4h6v-4Zm2 0v4h2v-4h-2Zm4 0v4h6v-4h-6Zm1-2h5V7a4 4 0 0 0-4-4h-1v1.4A4 4 0 0 0 15 7v1ZM8 16H3v1a4 4 0 0 0 4 4h1v-5Zm8 0v5h1a4 4 0 0 0 4-4v-1h-5Z"/></svg>',
    audio: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 14V10h3l5-5v14l-5-5H4Zm12.3 3.3-1.4-1.4a5.5 5.5 0 0 0 0-7.8l1.4-1.4a7.5 7.5 0 0 1 0 10.6Zm3.1 3.1L18 19a10 10 0 0 0 0-14l1.4-1.4a12 12 0 0 1 0 16.8Z"/></svg>',
    target: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 2h2v3a7 7 0 0 1 6 6h3v2h-3a7 7 0 0 1-6 6v3h-2v-3a7 7 0 0 1-6-6H2v-2h3a7 7 0 0 1 6-6V2Zm1 5a5 5 0 1 0 0 10 5 5 0 0 0 0-10Zm0 3a2 2 0 1 1 0 4 2 2 0 0 1 0-4Z"/></svg>',
    light: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 21h6v-2H9v2Zm3-19a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2Zm2.8 11.2-.8.6V15h-4v-1.2l-.8-.6A5 5 0 1 1 14.8 13.2Z"/></svg>',
    video: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5h18v12H3V5Zm2 2v8h14V7H5Zm4 12h6v2H9v-2Z"/></svg>',
    shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 4 5v6c0 5 3.4 9.7 8 11 4.6-1.3 8-6 8-11V5l-8-3Zm0 2.2 6 2.3V11c0 3.9-2.4 7.4-6 8.8A9.8 9.8 0 0 1 6 11V6.5l6-2.3Z"/></svg>',
    search: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10.5 4a6.5 6.5 0 1 0 4.1 11.5l4 4 1.4-1.4-4-4A6.5 6.5 0 0 0 10.5 4Zm0 2a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9Z"/></svg>',
    chevron: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 9 4 4 4-4 1.4 1.4L12 15.8l-5.4-5.4L8 9Z"/></svg>',
  };
  return icons[name] || icons.dashboard;
}

function loadSettings() {
  try {
    return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

function saveSettings() {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
}

function loadCollapsedVlans() {
  try {
    return new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveCollapsedVlans() {
  localStorage.setItem(COLLAPSE_KEY, JSON.stringify(Array.from(state.collapsedVlans)));
}

function defaultFilters() {
  return {
    search: "",
    vlan: "all",
    status: "all",
    source: "all",
    expected: "all",
  };
}

function loadFilters() {
  try {
    return { ...defaultFilters(), ...JSON.parse(localStorage.getItem(FILTERS_KEY) || "{}") };
  } catch {
    return defaultFilters();
  }
}

function saveFilters() {
  localStorage.setItem(FILTERS_KEY, JSON.stringify(state.filters));
}

function loadOpenDevices() {
  try {
    return new Set(JSON.parse(localStorage.getItem(OPEN_DEVICES_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveOpenDevices() {
  localStorage.setItem(OPEN_DEVICES_KEY, JSON.stringify(Array.from(state.openDevices)));
}

function text(value) {
  if (value === null || value === undefined || value === "") {
    return "Unknown";
  }
  return String(value);
}

function isUnknown(value) {
  return text(value).toLowerCase() === "unknown";
}

function normalize(value) {
  return text(value).toLowerCase();
}

function statusClass(status) {
  const normalized = normalize(status);
  if (["online", "offline", "warning", "critical", "problem"].includes(normalized)) {
    return normalized === "problem" ? "critical" : normalized;
  }
  return "unknown";
}

function formatStatus(status) {
  const normalized = text(status);
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function formatTime(value) {
  if (!value || isUnknown(value)) {
    return "Unknown";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? text(value) : date.toLocaleString();
}

function clearNode(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) {
    node.className = options.className;
  }
  if (options.text !== undefined) {
    node.textContent = options.text;
  }
  if (options.html !== undefined) {
    node.innerHTML = options.html;
  }
  if (options.attrs) {
    Object.entries(options.attrs).forEach(([key, value]) => {
      if (value !== null && value !== undefined) {
        node.setAttribute(key, String(value));
      }
    });
  }
  children.forEach((child) => node.appendChild(child));
  return node;
}

function setIconContainers() {
  document.querySelectorAll("[data-icon]").forEach((node) => {
    node.innerHTML = icon(node.dataset.icon);
  });
}

function statusDot(status) {
  return el("span", { className: `status-dot ${statusClass(status)}`, attrs: { "aria-hidden": "true" } });
}

function statusPill(status) {
  return el("span", { className: `status-pill ${statusClass(status)}`, text: formatStatus(status) });
}

function badge(label, tone = "neutral") {
  return el("span", { className: `badge ${tone}`, text: text(label) });
}

function webUrlFor(item) {
  if (item?.web_url && !isUnknown(item.web_url)) {
    return item.web_url;
  }
  if (item?.ip_address && !isUnknown(item.ip_address)) {
    return `http://${item.ip_address}`;
  }
  if (item?.ip && !isUnknown(item.ip)) {
    return `http://${item.ip}`;
  }
  return null;
}

function openWebAction(item, label = "Open web interface") {
  const href = webUrlFor(item);
  if (!href) {
    return el("span");
  }
  return el("a", {
    className: "action-button",
    html: icon("external"),
    attrs: {
      href,
      target: "_blank",
      rel: "noopener noreferrer",
      title: label,
      "aria-label": label,
    },
  });
}

function unknownLabel(value) {
  return el("span", {
    className: isUnknown(value) ? "value unknown-value" : "value",
    text: text(value),
  });
}

function applyTheme() {
  const requested = state.settings.theme;
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const resolved = requested === "system" ? (systemDark ? "dark" : "light") : requested;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themePreference = requested;
}

function applyDensity() {
  document.documentElement.dataset.compact = state.settings.compactMode ? "true" : "false";
}

function updateRefreshTimer() {
  if (state.refreshTimer) {
    window.clearInterval(state.refreshTimer);
  }
  state.refreshTimer = window.setInterval(loadStatus, Number(state.settings.refreshInterval));
}

function setRoute(route, options = {}) {
  state.route = PAGE_META[route] ? route : "overview";
  if (options.vlan !== undefined) {
    state.filters.vlan = String(options.vlan);
    saveFilters();
  }
  localStorage.setItem(ROUTE_KEY, state.route);
  window.location.hash = state.route;
  render();
  elements.pageContent.focus({ preventScroll: true });
}

function routeFromHash() {
  const route = window.location.hash.replace("#", "");
  if (PAGE_META[route]) {
    return route;
  }
  const savedRoute = localStorage.getItem(ROUTE_KEY);
  return PAGE_META[savedRoute] ? savedRoute : "overview";
}

function updateHeader() {
  const meta = PAGE_META[state.route] || PAGE_META.overview;
  elements.pageKicker.textContent = meta.kicker;
  elements.pageTitle.textContent = meta.title;
  elements.pageSubtitle.textContent = meta.subtitle;
  elements.navItems.forEach((item) => {
    item.classList.toggle("active", item.dataset.route === state.route);
  });

  const station = state.status?.station || {};
  elements.stationBadge.textContent = `${text(station.name || "Station")} / ${text(station.ip_address)}`;
}

function readiness(data) {
  const critical = [primaryRouter(data), primaryFohSwitch(data), ...stageSwitches(data)].filter(Boolean);
  const criticalDown = critical.filter((item) => item.status !== "online");
  const internetDown = data.internet?.status !== "online";
  const gatewayIssues = (data.vlans || []).filter((vlan) => vlan.gateway_status !== "online");
  const warnings = data.warnings || [];

  if (internetDown || criticalDown.length > 0) {
    return {
      status: "critical",
      title: "PROBLEM",
      message: "A critical network path component is not online.",
    };
  }

  if (warnings.length > 0 || gatewayIssues.length > 0) {
    return {
      status: "warning",
      title: "WARNINGS",
      message: "The show network is reachable, with issues that need attention.",
    };
  }

  return {
    status: "online",
    title: "SHOW NETWORK READY",
    message: "Critical checks are online and VLAN gateways are reachable.",
  };
}

function countByStatus(devices, status) {
  return devices.filter((device) => device.status === status).length;
}

function infraById(data, id) {
  return (data.infrastructure || []).find((item) => item.id === id);
}

function infrastructureItems(data) {
  return data.infrastructure || [];
}

function roleText(item) {
  return normalize(item?.role || item?.id || item?.name || "");
}

function isRouter(item) {
  const role = roleText(item);
  return item?.id === "lancom-router" || role.includes("router");
}

function isFohSwitch(item) {
  const role = roleText(item);
  const name = normalize(item?.name);
  return item?.id === "foh-switch" || role.includes("foh") || role.includes("core-switch") || name.includes("foh");
}

function isStageSwitch(item) {
  const role = roleText(item);
  const name = normalize(item?.name);
  return item?.id === "stage-switch" || item?.id?.startsWith("stage-switch") || role.includes("stage") || name.includes("stage");
}

function primaryRouter(data) {
  return infrastructureItems(data).find(isRouter) || infraById(data, "lancom-router");
}

function primaryFohSwitch(data) {
  return infrastructureItems(data).find(isFohSwitch) || infraById(data, "foh-switch");
}

function stageSwitches(data) {
  const stages = infrastructureItems(data).filter(isStageSwitch);
  return stages.length > 0 ? stages : [infraById(data, "stage-switch")].filter(Boolean);
}

function summaryCard(label, value, status, iconName, caption = "", actionItem = null) {
  return el("article", { className: "summary-card" }, [
    el("div", { className: "summary-icon", html: icon(iconName) }),
    el("div", { className: "summary-body" }, [
      el("span", { className: "summary-label", text: label }),
      el("strong", { text: text(value) }),
      caption ? el("span", { className: "summary-caption", text: caption }) : el("span"),
    ]),
    el("div", { className: "card-actions" }, [
      actionItem ? openWebAction(actionItem) : el("span"),
      statusPill(status),
    ]),
  ]);
}

function aggregateStatus(items) {
  const present = items.filter(Boolean);
  if (present.length === 0) return "unknown";
  if (present.some((item) => item.status === "offline")) return "offline";
  if (present.some((item) => item.status !== "online")) return "warning";
  return "online";
}

function topologyChain(data) {
  const router = primaryRouter(data);
  const foh = primaryFohSwitch(data);
  const stages = stageSwitches(data);
  return [
    { label: "Internet / 5G Uplink", role: "WAN", status: data.internet?.status || "unknown", ip: "External", icon: "internet" },
    router ? { label: router.name || "LANCOM 1783VAW", role: router.role || "Router", status: router.status, ip: router.ip_address, icon: "router", item: router } : null,
    foh ? { label: foh.name || "FOH AT-GS950/48", role: foh.role || "FOH core", status: foh.status, ip: foh.ip_address, icon: "switch", item: foh } : null,
    ...stages.map((stage, index) => ({
      label: stage.name || `Stage switch ${index + 1}`,
      role: stage.role || "Stage switch",
      status: stage.status,
      ip: stage.ip_address,
      icon: "switch",
      item: stage,
    })),
  ].filter(Boolean);
}

function renderOverview(data) {
  const ready = readiness(data);
  const devices = data.devices || [];
  const vlans = data.vlans || [];
  const gatewayOnline = vlans.filter((vlan) => vlan.gateway_status === "online").length;
  const router = primaryRouter(data);
  const foh = primaryFohSwitch(data);
  const stages = stageSwitches(data);
  const stageStatus = aggregateStatus(stages);

  const hero = el("section", { className: `ready-panel ${statusClass(ready.status)}` }, [
    el("div", { className: "ready-copy" }, [
      el("span", { className: "ready-kicker", text: "Current state" }),
      el("h3", { text: ready.title }),
      el("p", { text: ready.message }),
    ]),
    el("div", { className: "ready-meta" }, [
      el("span", { text: `${countByStatus(devices, "online")} devices online` }),
      el("span", { text: `${gatewayOnline}/${vlans.length} VLAN gateways` }),
    ]),
  ]);

  const warnings = renderWarningStack(data.warnings || []);
  const summary = el("section", { className: "summary-grid" }, [
    summaryCard("Internet", formatStatus(data.internet?.status || "unknown"), data.internet?.status, "internet", `${(data.internet?.probes || []).length} probes`),
    summaryCard("LANCOM Router", formatStatus(router?.status || "unknown"), router?.status, "router", text(router?.ip_address), router),
    summaryCard("FOH Switch", formatStatus(foh?.status || "unknown"), foh?.status, "switch", text(foh?.ip_address), foh),
    summaryCard(stages.length > 1 ? "Stage Switches" : "Stage Switch", formatStatus(stageStatus), stageStatus, "switch", stages.length > 1 ? `${stages.length} switches` : text(stages[0]?.ip_address), stages.length === 1 ? stages[0] : null),
    summaryCard("VLAN Gateways", `${gatewayOnline}/${vlans.length} Online`, gatewayOnline === vlans.length ? "online" : "warning", "shield", "Gateway reachability"),
    summaryCard("Devices", `${devices.length} discovered`, countByStatus(devices, "offline") > 0 ? "warning" : "online", "devices", `${countByStatus(devices, "online")} online`),
  ]);

  const compactTopology = el("section", { className: "panel" }, [
    sectionTitle("Network Path", "Compact topology overview."),
    el("div", { className: "compact-topology" }, topologyChain(data).map((node, index, nodes) => compactTopologyNode(node, index < nodes.length - 1))),
    el("button", { className: "link-button", text: "View full topology", attrs: { type: "button", "data-route-link": "topology" } }),
  ]);

  const vlanCards = el("section", { className: "panel" }, [
    sectionTitle("VLAN Summary", "Click a VLAN to open matching devices."),
    el("div", { className: "vlan-card-grid" }, (data.devices_by_vlan || []).map((group) => vlanSummaryCard(group, data))),
  ]);

  elements.pageContent.append(hero, warnings, summary, compactTopology, vlanCards);
}

function compactTopologyNode(node, showArrow) {
  return el("div", { className: "compact-topology-step" }, [
    el("article", { className: "compact-topology-node" }, [
      el("span", { className: "topology-icon", html: icon(node.icon) }),
      el("div", { className: "compact-node-copy" }, [
        el("strong", { text: node.label }),
        el("span", { text: `${node.role} / ${text(node.ip)}` }),
      ]),
      statusDot(node.status),
      node.item ? openWebAction(node.item) : el("span"),
    ]),
    showArrow ? el("span", { className: "compact-arrow", html: icon("arrow"), attrs: { "aria-hidden": "true" } }) : el("span"),
  ]);
}

function renderWarningStack(warnings) {
  const panel = el("section", { className: "warning-stack" });
  if (warnings.length === 0) {
    panel.append(el("div", { className: "notice online" }, [
      statusDot("online"),
      el("span", { text: "No active warnings." }),
    ]));
    return panel;
  }

  warnings.forEach((warning) => {
    panel.append(el("div", { className: `notice ${statusClass(warning.severity || "warning")}` }, [
      statusDot(warning.severity || "warning"),
      el("span", { text: warning.message || String(warning) }),
    ]));
  });
  return panel;
}

function sectionTitle(title, subtitle = "") {
  return el("div", { className: "section-title" }, [
    el("div", {}, [
      el("h3", { text: title }),
      subtitle ? el("p", { text: subtitle }) : el("p"),
    ]),
  ]);
}

function vlanIconName(name) {
  const key = normalize(name);
  if (key.includes("control")) return "command";
  if (key.includes("audio")) return "audio";
  if (key.includes("laser")) return "target";
  if (key.includes("lighting")) return "light";
  if (key.includes("video")) return "video";
  if (key.includes("mgmt")) return "shield";
  return "topology";
}

function vlanStatus(group, data) {
  const vlan = (data.vlans || []).find((item) => item.id === group.id);
  return vlan?.gateway_status || (group.counts?.offline ? "warning" : "online");
}

function vlanSummaryCard(group, data) {
  const status = vlanStatus(group, data);
  const card = el("button", {
    className: "vlan-summary-card",
    attrs: { type: "button", "data-vlan-link": group.id },
  }, [
    el("div", { className: "vlan-summary-head" }, [
      el("span", { className: "vlan-icon", html: icon(vlanIconName(group.name)) }),
      el("div", {}, [
        el("strong", { text: group.name }),
        el("span", { text: `VLAN ${group.id} / ${group.subnet}` }),
      ]),
      statusDot(status),
    ]),
    el("div", { className: "mini-counts" }, [
      miniCount("Total", group.counts?.known ?? 0, "Known devices"),
      miniCount("Up", group.counts?.online ?? 0, "Online devices"),
      miniCount("Down", group.counts?.offline ?? 0, "Offline devices"),
      miniCount("Unk", group.counts?.unknown ?? 0, "Unknown status devices"),
    ]),
  ]);
  return card;
}

function miniCount(label, value, title = label) {
  return el("span", { className: "mini-count", attrs: { title } }, [
    el("strong", { text: value }),
    el("span", { text: label }),
  ]);
}

function filteredDevices(data) {
  const search = normalize(state.filters.search);
  const source = state.filters.source;
  return (data.devices || []).filter((device) => {
    const haystack = [
      device.display_name,
      device.name,
      device.ip_address,
      device.mac_address,
      device.hostname,
      device.vendor,
      device.role,
      device.vlan_name,
      ...(device.discovery_sources || []),
    ].map(normalize).join(" ");

    const isUnknownDevice = !device.expected && !(device.discovery_sources || []).includes("inventory");
    const unknownLocation = isUnknown(device.connected_switch) || isUnknown(device.connected_port);

    if (!state.settings.showUnknownDevices && isUnknownDevice) return false;
    if (!state.settings.showOfflineDevices && device.status === "offline") return false;
    if (search && !haystack.includes(search)) return false;
    if (state.filters.vlan !== "all" && String(device.vlan_id) !== state.filters.vlan) return false;
    if (state.filters.status !== "all" && device.status !== state.filters.status) return false;
    if (source !== "all" && !(device.discovery_sources || []).includes(source)) return false;
    if (state.filters.expected === "expected" && !device.expected) return false;
    if (state.filters.expected === "unexpected" && device.expected) return false;
    if (state.filters.expected === "unknown-location" && !unknownLocation) return false;
    return true;
  });
}

function renderDevices(data) {
  const devices = filteredDevices(data);
  const groups = groupFilteredDevices(data.devices_by_vlan || [], devices);
  const sources = Array.from(new Set((data.devices || []).flatMap((device) => device.discovery_sources || []))).sort();
  const visibleTotal = devices.length;

  elements.pageContent.append(
    renderDeviceFilters(data, sources),
    el("section", { className: "device-summary-strip" }, [
      metricChip("Visible", visibleTotal),
      metricChip("Online", countByStatus(devices, "online")),
      metricChip("Offline", countByStatus(devices, "offline")),
      metricChip("Unknown", countByStatus(devices, "unknown")),
    ]),
  );

  const list = el("section", { className: "device-groups" });
  groups.forEach((group) => list.append(deviceGroup(group)));
  if (groups.length === 0) {
    list.append(emptyState("No devices match the current filters."));
  }
  elements.pageContent.append(list);
}

function renderDeviceFilters(data, sources) {
  const vlans = data.devices_by_vlan || [];
  return el("section", { className: "filter-panel" }, [
    el("label", { className: "search-field" }, [
      el("span", { html: icon("search") }),
      el("input", {
        attrs: {
          id: "deviceSearch",
          type: "search",
          placeholder: "Search devices, IPs, hostnames, sources",
          value: state.filters.search,
        },
      }),
    ]),
    selectField("VLAN", "vlanFilter", state.filters.vlan, [["all", "All VLANs"], ...vlans.map((group) => [String(group.id), `${group.name} / ${group.id}`])]),
    selectField("Status", "statusFilter", state.filters.status, [["all", "All statuses"], ["online", "Online"], ["offline", "Offline"], ["unknown", "Unknown"]]),
    selectField("Source", "sourceFilter", state.filters.source, [["all", "All sources"], ...sources.map((item) => [item, item])]),
    selectField("Type", "expectedFilter", state.filters.expected, [["all", "All devices"], ["expected", "Expected"], ["unexpected", "Discovered only"], ["unknown-location", "Unknown location"]]),
  ]);
}

function selectField(label, id, value, options) {
  const select = el("select", { attrs: { id } });
  options.forEach(([optionValue, optionLabel]) => {
    const option = el("option", { text: optionLabel, attrs: { value: optionValue } });
    if (String(optionValue) === String(value)) {
      option.selected = true;
    }
    select.append(option);
  });
  return el("label", { className: "select-field" }, [
    el("span", { text: label }),
    select,
  ]);
}

function groupFilteredDevices(groups, devices) {
  const byIp = new Set(devices.map((device) => device.ip_address));
  return groups.map((group) => ({
    ...group,
    devices: (group.devices || []).filter((device) => byIp.has(device.ip_address)),
    counts: {
      known: (group.devices || []).filter((device) => byIp.has(device.ip_address)).length,
      online: (group.devices || []).filter((device) => byIp.has(device.ip_address) && device.status === "online").length,
      offline: (group.devices || []).filter((device) => byIp.has(device.ip_address) && device.status === "offline").length,
      unknown: (group.devices || []).filter((device) => byIp.has(device.ip_address) && device.status === "unknown").length,
    },
  })).filter((group) => group.devices.length > 0 || state.filters.vlan === String(group.id));
}

function metricChip(label, value) {
  return el("div", { className: "metric-chip" }, [
    el("strong", { text: value }),
    el("span", { text: label }),
  ]);
}

function deviceGroup(group) {
  const collapsed = state.collapsedVlans.has(String(group.id));
  const section = el("section", { className: `device-group ${collapsed ? "collapsed" : ""}` });
  section.append(el("button", {
    className: "device-group-toggle",
    attrs: { type: "button", "data-vlan-toggle": group.id },
  }, [
    el("span", { className: "chevron", html: icon("chevron") }),
    el("span", { className: "vlan-icon", html: icon(vlanIconName(group.name)) }),
    el("span", { className: "group-name", text: `${group.name} / VLAN ${group.id}` }),
    el("span", { className: "group-subnet", text: group.subnet }),
    badge(`${group.counts.known} visible`, "neutral"),
  ]));

  const rows = el("div", { className: "device-list" });
  group.devices.forEach((device) => rows.append(deviceRow(device)));
  section.append(rows);
  return section;
}

function deviceKey(device) {
  return String(device.ip_address || device.mac_address || device.id || device.display_name || device.name);
}

function deviceRow(device) {
  const key = deviceKey(device);
  const detailId = `device-${key.replaceAll(".", "-").replaceAll(":", "-").replaceAll(" ", "-")}`;
  const row = el("details", {
    className: "device-row",
    attrs: { id: detailId, "data-device-key": key },
  });
  if (state.openDevices.has(key)) {
    row.open = true;
  }
  row.append(el("summary", { className: "device-summary" }, [
    statusDot(device.status),
    el("div", { className: "device-primary" }, [
      el("strong", { text: device.display_name || device.name || device.ip_address }),
      el("span", { text: text(device.ip_address) }),
    ]),
    el("span", { className: "device-vlan", text: `${text(device.vlan_name)} / ${text(device.vlan_id)}` }),
    badge(device.role || firstSource(device), device.expected ? "expected" : "neutral"),
    statusPill(device.status),
    openWebAction(device),
  ]));
  row.append(el("div", { className: "device-details" }, [
    detailItem("MAC", device.mac_address),
    detailItem("Hostname", device.hostname),
    detailItem("Last seen", formatTime(device.last_seen)),
    detailItem("Discovery", (device.discovery_sources || ["Unknown"]).join(", ")),
    detailItem("Connected switch", device.connected_switch),
    detailItem("Switch port", device.connected_port),
    detailItem("Confidence", device.switch_port_confidence),
  ]));
  return row;
}

function firstSource(device) {
  return (device.discovery_sources || ["Unknown"])[0];
}

function detailItem(label, value) {
  return el("div", { className: "detail-item" }, [
    el("span", { text: label }),
    unknownLabel(value),
  ]);
}

function renderTopologyPage(data) {
  const chain = topologyChain(data);

  const path = el("section", { className: "topology-path" });
  chain.forEach((node, index) => {
    path.append(topologyNode(node));
    if (index < chain.length - 1) {
      path.append(el("div", { className: "path-connector", attrs: { "aria-hidden": "true" } }));
    }
  });

  const lanes = el("section", { className: "panel" }, [
    sectionTitle("VLAN Lanes", "Counts only here; open Devices for host-level detail."),
    el("div", { className: "vlan-lane-grid" }, (data.devices_by_vlan || []).map((group) => vlanLane(group, data))),
  ]);

  elements.pageContent.append(el("div", { className: "topology-layout" }, [path, lanes]));
}

function topologyNode(node) {
  return el("article", { className: "topology-card" }, [
    el("span", { className: "topology-icon", html: icon(node.icon) }),
    el("div", { className: "topology-card-main" }, [
      el("strong", { text: node.label }),
      el("span", { text: `${node.role} / ${text(node.ip)}` }),
    ]),
    node.item ? openWebAction(node.item) : el("span"),
    statusPill(node.status),
  ]);
}

function vlanLane(group, data) {
  const status = vlanStatus(group, data);
  return el("button", {
    className: "vlan-lane",
    attrs: { type: "button", "data-vlan-link": group.id },
  }, [
    el("span", { className: "vlan-icon", html: icon(vlanIconName(group.name)) }),
    el("div", {}, [
      el("strong", { text: `${group.name} / VLAN ${group.id}` }),
      el("span", { text: `${group.subnet} / ${group.counts.known} devices` }),
    ]),
    statusDot(status),
  ]);
}

function renderSettings(data) {
  const discovery = data.discovery || {};
  elements.pageContent.append(
    el("section", { className: "settings-grid" }, [
      settingsCard("Appearance", [
        segmentedSetting("Theme", "theme", state.settings.theme, [["system", "System"], ["dark", "Dark"], ["light", "Light"]]),
        toggleSetting("Compact mode", "compactMode", state.settings.compactMode, "Use tighter spacing across cards and lists."),
      ]),
      settingsCard("Refresh", [
        segmentedSetting("Interval", "refreshInterval", String(state.settings.refreshInterval), [["5000", "5s"], ["10000", "10s"], ["30000", "30s"], ["60000", "60s"]]),
      ]),
      settingsCard("Discovery Display", [
        toggleSetting("Show unknown devices", "showUnknownDevices", state.settings.showUnknownDevices, "Show hosts discovered only by collectors."),
        toggleSetting("Show offline devices", "showOfflineDevices", state.settings.showOfflineDevices, "Keep offline hosts visible in the Devices page."),
      ]),
      settingsCard("Runtime", [
        readonlyLine("App version", APP_VERSION),
        readonlyLine("Discovery", `${text(discovery.status)} / ${(discovery.subnets || []).length} subnets`),
        readonlyLine("SNMP mapping", "Not implemented yet"),
        readonlyLine("Generated", formatTime(data.generated_at)),
      ]),
    ]),
  );
}

function settingsCard(title, children) {
  return el("article", { className: "settings-card" }, [
    el("h3", { text: title }),
    ...children,
  ]);
}

function segmentedSetting(label, key, value, options) {
  const group = el("div", { className: "setting-row" }, [
    el("span", { className: "setting-label", text: label }),
    el("div", { className: "segmented", attrs: { "data-setting": key } }),
  ]);
  const segmented = group.querySelector(".segmented");
  options.forEach(([optionValue, optionLabel]) => {
    segmented.append(el("button", {
      className: String(value) === String(optionValue) ? "active" : "",
      text: optionLabel,
      attrs: { type: "button", "data-setting-value": optionValue },
    }));
  });
  return group;
}

function toggleSetting(label, key, checked, help) {
  const input = el("input", { attrs: { type: "checkbox", "data-toggle-setting": key } });
  input.checked = Boolean(checked);
  return el("label", { className: "toggle-row" }, [
    el("span", {}, [
      el("strong", { text: label }),
      el("small", { text: help }),
    ]),
    input,
    el("span", { className: "toggle-visual" }),
  ]);
}

function readonlyLine(label, value) {
  return el("div", { className: "readonly-row" }, [
    el("span", { text: label }),
    unknownLabel(value),
  ]);
}

function emptyState(message) {
  return el("div", { className: "empty-state" }, [
    el("strong", { text: message }),
  ]);
}

function render() {
  clearNode(elements.pageContent);
  updateHeader();

  if (state.error) {
    elements.pageContent.append(el("div", { className: "notice critical" }, [
      statusDot("critical"),
      el("span", { text: state.error.message || "Status request failed." }),
    ]));
  }

  if (!state.status) {
    if (!state.error) {
      elements.pageContent.append(emptyState(state.loading ? "Loading network status." : "No status loaded yet."));
    }
    return;
  }

  if (state.route === "devices") {
    renderDevices(state.status);
  } else if (state.route === "topology") {
    renderTopologyPage(state.status);
  } else if (state.route === "settings") {
    renderSettings(state.status);
  } else {
    renderOverview(state.status);
  }
}

async function loadStatus() {
  state.loading = true;
  elements.refreshButton.disabled = true;
  if (!state.status) {
    render();
  }

  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Status request failed: HTTP ${response.status}`);
    }
    state.status = await response.json();
    state.error = null;
    elements.lastUpdated.textContent = `Updated ${formatTime(state.status.generated_at)}`;
  } catch (error) {
    state.error = error;
    elements.lastUpdated.textContent = "Status request failed";
  } finally {
    state.loading = false;
    elements.refreshButton.disabled = false;
    render();
  }
}

function updateFilter(key, value) {
  const searchInput = document.querySelector("#deviceSearch");
  const selectionStart = key === "search" && searchInput ? searchInput.selectionStart : null;
  state.filters[key] = value;
  saveFilters();
  render();

  if (key === "search") {
    const refreshedInput = document.querySelector("#deviceSearch");
    if (refreshedInput) {
      refreshedInput.focus();
      const position = selectionStart ?? refreshedInput.value.length;
      refreshedInput.setSelectionRange(position, position);
    }
  }
}

function updateSetting(key, value) {
  if (key === "refreshInterval") {
    state.settings[key] = Number(value);
    updateRefreshTimer();
  } else {
    state.settings[key] = value;
  }
  saveSettings();
  applyTheme();
  applyDensity();
  render();
}

function bindEvents() {
  elements.navItems.forEach((item) => {
    item.addEventListener("click", () => setRoute(item.dataset.route));
  });

  elements.settingsButton.addEventListener("click", () => setRoute("settings"));
  elements.refreshButton.addEventListener("click", loadStatus);

  elements.pageContent.addEventListener("input", (event) => {
    const target = event.target;
    if (target.id === "deviceSearch") updateFilter("search", target.value);
    if (target.dataset.toggleSetting) updateSetting(target.dataset.toggleSetting, target.checked);
  });

  elements.pageContent.addEventListener("change", (event) => {
    const target = event.target;
    if (target.id === "vlanFilter") updateFilter("vlan", target.value);
    if (target.id === "statusFilter") updateFilter("status", target.value);
    if (target.id === "sourceFilter") updateFilter("source", target.value);
    if (target.id === "expectedFilter") updateFilter("expected", target.value);
  });

  elements.pageContent.addEventListener("click", (event) => {
    const action = event.target.closest(".action-button");
    if (action) {
      event.stopPropagation();
      return;
    }

    const routeLink = event.target.closest("[data-route-link]");
    if (routeLink) {
      setRoute(routeLink.dataset.routeLink);
      return;
    }

    const vlanLink = event.target.closest("[data-vlan-link]");
    if (vlanLink) {
      setRoute("devices", { vlan: vlanLink.dataset.vlanLink });
      return;
    }

    const toggle = event.target.closest("[data-vlan-toggle]");
    if (toggle) {
      const id = String(toggle.dataset.vlanToggle);
      if (state.collapsedVlans.has(id)) {
        state.collapsedVlans.delete(id);
      } else {
        state.collapsedVlans.add(id);
      }
      saveCollapsedVlans();
      render();
      return;
    }

    const settingButton = event.target.closest("[data-setting-value]");
    if (settingButton) {
      const group = settingButton.closest("[data-setting]");
      updateSetting(group.dataset.setting, settingButton.dataset.settingValue);
    }
  });

  elements.pageContent.addEventListener("toggle", (event) => {
    const row = event.target.closest?.(".device-row");
    if (!row) {
      return;
    }
    const key = row.dataset.deviceKey;
    if (!key) {
      return;
    }
    if (row.open) {
      state.openDevices.add(key);
    } else {
      state.openDevices.delete(key);
    }
    saveOpenDevices();
  }, true);

  window.addEventListener("hashchange", () => {
    state.route = routeFromHash();
    localStorage.setItem(ROUTE_KEY, state.route);
    render();
  });

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (state.settings.theme === "system") {
      applyTheme();
    }
  });
}

function init() {
  state.route = routeFromHash();
  localStorage.setItem(ROUTE_KEY, state.route);
  applyTheme();
  applyDensity();
  setIconContainers();
  bindEvents();
  updateRefreshTimer();
  render();
  loadStatus();
}

init();

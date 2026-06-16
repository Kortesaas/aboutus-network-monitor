const APP_VERSION = "0.8.0";
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
    title: "Overview",
  },
  devices: {
    title: "Devices",
  },
  topology: {
    title: "Topology",
  },
  switches: {
    title: "Switches",
  },
  settings: {
    title: "Settings",
  },
};

const elements = {
  navItems: Array.from(document.querySelectorAll(".nav-item")),
  navIcons: Array.from(document.querySelectorAll("[data-icon]")),
  pageTitle: document.querySelector("#pageTitle"),
  pageContent: document.querySelector("#pageContent"),
  systemClock: document.querySelector("#systemClock"),
  refreshCountdown: document.querySelector("#refreshCountdown"),
  refreshProgress: document.querySelector("#refreshProgress"),
  lastUpdated: document.querySelector("#lastUpdated"),
  refreshButton: document.querySelector("#refreshButton"),
  settingsButton: document.querySelector("#settingsButton"),
  portTooltip: null,
};

const state = {
  route: "overview",
  status: null,
  error: null,
  loading: false,
  refreshTimer: null,
  clockTimer: null,
  refreshCycleStartedAt: null,
  nextRefreshAt: null,
  settings: loadSettings(),
  collapsedVlans: loadCollapsedVlans(),
  openDevices: loadOpenDevices(),
  filters: loadFilters(),
  selectedPorts: {},
  seenEventKeys: new Set(),
  eventListInitialized: false,
};

function icon(name) {
  const icons = {
    dashboard: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 13h6V4H4v9Zm10 7h6V4h-6v16ZM4 20h6v-5H4v5Z"/></svg>',
    devices: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H4V5Zm2 2v7h12V7H6Zm3 12h6v2H9v-2Zm-4 0h14v2H5v-2Z"/></svg>',
    network: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4.8a7.2 7.2 0 1 0 0 14.4 7.2 7.2 0 0 0 0-14.4Zm-3.9 6.3a9.8 9.8 0 0 1 .7-3.3 5.5 5.5 0 0 1 2.3-1v4.3h-3Zm0 1.8h3v4.3a5.5 5.5 0 0 1-2.3-1 9.8 9.8 0 0 1-.7-3.3Zm4.8 4.3v-4.3h3a9.8 9.8 0 0 1-.7 3.3 5.5 5.5 0 0 1-2.3 1Zm3-6.1h-3V6.8a5.5 5.5 0 0 1 2.3 1 9.8 9.8 0 0 1 .7 3.3Zm1.8 1.8h1.1a5.3 5.3 0 0 1-1.8 3.1 12 12 0 0 0 .7-3.1Zm1.1-1.8h-1.1A12 12 0 0 0 17 8a5.3 5.3 0 0 1 1.8 3.1Zm-11.8 0H5.9A5.3 5.3 0 0 1 7.7 8a12 12 0 0 0-.7 3.1Zm-1.1 1.8H7a12 12 0 0 0 .7 3.1 5.3 5.3 0 0 1-1.8-3.1ZM12 1.5a1.2 1.2 0 1 1 0 2.4 1.2 1.2 0 0 1 0-2.4Zm0 18.6a1.2 1.2 0 1 1 0 2.4 1.2 1.2 0 0 1 0-2.4ZM3.9 4.7a1.2 1.2 0 1 1 1.7 1.7 1.2 1.2 0 0 1-1.7-1.7Zm14.5 12.9a1.2 1.2 0 1 1 1.7 1.7 1.2 1.2 0 0 1-1.7-1.7ZM1.5 12a1.2 1.2 0 1 1 2.4 0 1.2 1.2 0 0 1-2.4 0Zm18.6 0a1.2 1.2 0 1 1 2.4 0 1.2 1.2 0 0 1-2.4 0ZM3.9 19.3a1.2 1.2 0 1 1 1.7-1.7 1.2 1.2 0 0 1-1.7 1.7ZM18.4 6.4a1.2 1.2 0 1 1 1.7-1.7 1.2 1.2 0 0 1-1.7 1.7Z"/></svg>',
    topology: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 3h2v4h-2V3Zm0 14h2v4h-2v-4ZM5 9h14v6H5V9Zm2 2v2h10v-2H7ZM3 11H1V7h6v2H3v2Zm20 0h-2V9h-4V7h6v4Z"/></svg>',
    settings: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.4 13.5c.1-.5.1-1 .1-1.5s0-1-.1-1.5l2-1.5-2-3.5-2.4 1a7.5 7.5 0 0 0-2.6-1.5L14 2h-4l-.4 2.5A7.5 7.5 0 0 0 7 6L4.6 5l-2 3.5 2 1.5c-.1.5-.1 1-.1 1.5s0 1 .1 1.5l-2 1.5 2 3.5 2.4-1a7.5 7.5 0 0 0 2.6 1.5L10 22h4l.4-2.5A7.5 7.5 0 0 0 17 18l2.4 1 2-3.5-2-1.5ZM12 15.5A3.5 3.5 0 1 1 12 8a3.5 3.5 0 0 1 0 7.5Z"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17.7 6.3A8 8 0 1 0 20 12h-2a6 6 0 1 1-1.8-4.3L13 11h8V3l-3.3 3.3Z"/></svg>',
    clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 2a8 8 0 1 1 0 16 8 8 0 0 1 0-16Zm1 3h-2v6l5 3 1-1.7-4-2.3V7Z"/></svg>',
    timer: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 2h6v2H9V2Zm2 6h2v5h-2V8Zm1-3a8 8 0 1 0 5.7 2.4L19 6l-1.4-1.4-1.4 1.3A8 8 0 0 0 12 5Zm0 2a6 6 0 1 1 0 12 6 6 0 0 1 0-12Z"/></svg>',
    external: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3h7v7h-2V6.4l-8.3 8.3-1.4-1.4L17.6 5H14V3ZM5 5h7v2H7v10h10v-5h2v7H5V5Z"/></svg>',
    arrow: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 5.6 19.4 12 13 18.4 11.6 17l4-4H4v-2h11.6l-4-4L13 5.6Z"/></svg>',
    internet: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm6.9 9h-3.1a15 15 0 0 0-1.1-5 8.1 8.1 0 0 1 4.2 5ZM12 4.1c.7 1 1.4 3.3 1.7 6.9h-3.4c.3-3.6 1-5.9 1.7-6.9ZM4.3 13h3.9c.1 1.7.4 3.3.8 4.7A8.1 8.1 0 0 1 4.3 13Zm3.9-2H4.3A8.1 8.1 0 0 1 9 6.3 18 18 0 0 0 8.2 11Zm3.8 8.9c-.7-1-1.4-3.3-1.7-6.9h3.4c-.3 3.6-1 5.9-1.7 6.9Zm3-2.2c.4-1.4.7-3 .8-4.7h3.9a8.1 8.1 0 0 1-4.7 4.7Z"/></svg>',
    dns: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h16v6H4V4Zm2 2v2h12V6H6Zm-2 8h16v6H4v-6Zm2 2v2h12v-2H6Zm1-9h2v2H7V7Zm0 10h2v2H7v-2Zm5-4h2v2h-2v-2Zm0-3h2v3h-2v-3Z"/></svg>',
    history: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 1 1-8.5 6H1l3.5-4L8 9H5.6A7 7 0 1 0 12 5v4l4 2-1 1.7-5-2.9V3h2Z"/></svg>',
    alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 1.7 20h20.6L12 2Zm0 4 6.8 12H5.2L12 6Zm-1 4h2v4h-2v-4Zm0 5h2v2h-2v-2Z"/></svg>',
    router: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 11h16v8H4v-8Zm2 2v4h12v-4H6Zm1 3h2v-2H7v2Zm4 0h2v-2h-2v2Zm7-9 1.4-1.4A10.5 10.5 0 0 0 12 2a10.5 10.5 0 0 0-7.4 3.1L6 6.5A8.5 8.5 0 0 1 12 4c2.3 0 4.4.9 6 3Zm-3 3 1.4-1.4A6.2 6.2 0 0 0 12 6.8c-1.7 0-3.2.7-4.4 1.8L9 10a4.3 4.3 0 0 1 6 0Z"/></svg>',
    switch: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7h18v10H3V7Zm2 2v6h14V9H5Zm1 5h2v-2H6v2Zm3 0h2v-2H9v2Zm3 0h2v-2h-2v2Zm3 0h2v-2h-2v2ZM7 4h10v2H7V4Zm0 14h10v2H7v-2Z"/></svg>',
    command: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3a4 4 0 0 0-4 4v1h5V7a4 4 0 0 0-1-2.6V3Zm2 7H3v4h6v-4Zm2 0v4h2v-4h-2Zm4 0v4h6v-4h-6Zm1-2h5V7a4 4 0 0 0-4-4h-1v1.4A4 4 0 0 0 15 7v1ZM8 16H3v1a4 4 0 0 0 4 4h1v-5Zm8 0v5h1a4 4 0 0 0 4-4v-1h-5Z"/></svg>',
    audio: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 14V10h3l5-5v14l-5-5H4Zm12.3 3.3-1.4-1.4a5.5 5.5 0 0 0 0-7.8l1.4-1.4a7.5 7.5 0 0 1 0 10.6Zm3.1 3.1L18 19a10 10 0 0 0 0-14l1.4-1.4a12 12 0 0 1 0 16.8Z"/></svg>',
    target: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 2h2v3a7 7 0 0 1 6 6h3v2h-3a7 7 0 0 1-6 6v3h-2v-3a7 7 0 0 1-6-6H2v-2h3a7 7 0 0 1 6-6V2Zm1 5a5 5 0 1 0 0 10 5 5 0 0 0 0-10Zm0 3a2 2 0 1 1 0 4 2 2 0 0 1 0-4Z"/></svg>',
    light: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 21h6v-2H9v2Zm3-19a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2Zm2.8 11.2-.8.6V15h-4v-1.2l-.8-.6A5 5 0 1 1 14.8 13.2Z"/></svg>',
    video: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5h18v12H3V5Zm2 2v8h14V7H5Zm4 12h6v2H9v-2Z"/></svg>',
    shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 4 5v6c0 5 3.4 9.7 8 11 4.6-1.3 8-6 8-11V5l-8-3Zm0 2.2 6 2.3V11c0 3.9-2.4 7.4-6 8.8A9.8 9.8 0 0 1 6 11V6.5l6-2.3Z"/></svg>',
    search: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10.5 4a6.5 6.5 0 1 0 4.1 11.5l4 4 1.4-1.4-4-4A6.5 6.5 0 0 0 10.5 4Zm0 2a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9Z"/></svg>',
    mapPin: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a7 7 0 0 0-7 7c0 5.2 7 13 7 13s7-7.8 7-13a7 7 0 0 0-7-7Zm0 2a5 5 0 0 1 5 5c0 2.8-3 7.3-5 9.8C10 16.3 7 11.8 7 9a5 5 0 0 1 5-5Zm0 3a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z"/></svg>',
    chip: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3h8v3h3v12h-3v3H8v-3H5V6h3V3Zm2 2v3H7v8h3v3h4v-3h3V8h-3V5h-4Zm0 5h4v4h-4v-4ZM2 8h2v2H2V8Zm0 6h2v2H2v-2Zm18-6h2v2h-2V8Zm0 6h2v2h-2v-2Z"/></svg>',
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

function filtersActive() {
  const defaults = defaultFilters();
  return Object.keys(defaults).some((key) => String(state.filters[key] ?? "") !== String(defaults[key]));
}

function clearFilters() {
  state.filters = defaultFilters();
  saveFilters();
  render();
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

function formatRelativeTime(value) {
  if (!value || isUnknown(value)) {
    return "Unknown";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return text(value);
  }
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function formatEventTimestamp(value) {
  if (!value || isUnknown(value)) {
    return "Unknown";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return text(value);
  }
  return date.toLocaleString([], {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
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

function refreshIntervalMs() {
  return Math.max(1000, Number(state.settings.refreshInterval) || DEFAULT_SETTINGS.refreshInterval);
}

function formatClock(value) {
  return value.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function updateStatusBar() {
  const now = Date.now();
  elements.systemClock.textContent = formatClock(new Date(now));

  const interval = refreshIntervalMs();
  const remaining = state.nextRefreshAt ? Math.max(0, state.nextRefreshAt - now) : interval;
  const progress = state.refreshCycleStartedAt
    ? Math.min(100, Math.max(0, ((now - state.refreshCycleStartedAt) / interval) * 100))
    : 0;

  elements.refreshCountdown.textContent = state.loading ? "Refreshing" : `Refresh ${Math.ceil(remaining / 1000)}s`;
  elements.refreshProgress.style.setProperty("--progress", `${state.loading ? 100 : progress}%`);
}

function updateClockTimer() {
  if (state.clockTimer) {
    window.clearInterval(state.clockTimer);
  }
  state.clockTimer = window.setInterval(updateStatusBar, 250);
  updateStatusBar();
}

function scheduleNextRefresh() {
  if (state.refreshTimer) {
    window.clearTimeout(state.refreshTimer);
  }
  const interval = refreshIntervalMs();
  state.refreshCycleStartedAt = Date.now();
  state.nextRefreshAt = state.refreshCycleStartedAt + interval;
  state.refreshTimer = window.setTimeout(() => loadStatus({ preserveScroll: true, forceRefresh: true }), interval);
  updateStatusBar();
}

function updateRefreshTimer() {
  scheduleNextRefresh();
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
  elements.pageTitle.textContent = meta.title;
  elements.navItems.forEach((item) => {
    item.classList.toggle("active", item.dataset.route === state.route);
  });
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
      title: "Problem",
    };
  }

  if (warnings.length > 0 || gatewayIssues.length > 0) {
    return {
      status: "warning",
      title: "Warnings",
    };
  }

  return {
    status: "online",
    title: "Ready",
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

function dnsProbes(data) {
  return (data.internet?.probes || []).filter((probe) => normalize(probe.name).includes("dns"));
}

function dnsProbeCard(probe) {
  const check = probe.check || {};
  const latency = check.latency_ms !== null && check.latency_ms !== undefined ? `${check.latency_ms} ms` : "Unknown";
  return el("article", { className: "probe-check-card" }, [
    el("span", { className: "probe-check-icon", html: icon("dns") }),
    el("div", { className: "probe-check-main" }, [
      el("strong", { text: text(probe.name) }),
      el("span", { text: text(check.target) }),
    ]),
    el("div", { className: "probe-check-meta" }, [
      el("span", { text: latency }),
      statusPill(probe.status),
    ]),
  ]);
}

function dnsChecksPanel(data) {
  const probes = dnsProbes(data);
  if (probes.length === 0) {
    return null;
  }
  return el("section", { className: "panel" }, [
    sectionTitle("DNS Servers"),
    el("div", { className: "probe-check-grid" }, probes.map(dnsProbeCard)),
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
      el("h3", { text: ready.title }),
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
    summaryCard("VLAN Gateways", `${gatewayOnline}/${vlans.length} Online`, gatewayOnline === vlans.length ? "online" : "warning", "shield"),
    summaryCard("Devices", `${devices.length} discovered`, countByStatus(devices, "offline") > 0 ? "warning" : "online", "devices", `${countByStatus(devices, "online")} online`),
  ]);
  const operatorPanels = el("section", { className: "operator-grid" }, [
    problemDevicesPanel(data),
    recentEventsPanel(data),
  ]);
  const dnsChecks = dnsChecksPanel(data);

  const compactTopology = el("section", { className: "panel" }, [
    sectionTitle("Network Path"),
    overviewTopologyGraph(data),
    el("button", { className: "link-button", text: "View full topology", attrs: { type: "button", "data-route-link": "topology" } }),
  ]);

  const vlanCards = el("section", { className: "panel" }, [
    sectionTitle("VLANs"),
    el("div", { className: "vlan-card-grid" }, (data.devices_by_vlan || []).map((group) => vlanSummaryCard(group, data))),
  ]);

  elements.pageContent.append(...[hero, warnings, summary, operatorPanels, dnsChecks, compactTopology, vlanCards].filter(Boolean));
}

function problemDevices(data) {
  return (data.devices || []).filter((device) => device.status !== "online").slice(0, 8);
}

function problemDevicesPanel(data) {
  const devices = problemDevices(data);
  const rows = devices.length > 0
    ? devices.map(problemDeviceRow)
    : [el("div", { className: "compact-empty", text: "No problem devices" })];
  return el("section", { className: "panel compact-panel" }, [
    sectionTitle("Problem Devices"),
    el("div", { className: "problem-list" }, rows),
    el("div", { className: "quick-actions" }, [
      quickFilterButton("Offline", { status: "offline" }),
      quickFilterButton("Unknown", { status: "unknown" }),
      quickFilterButton("Location", { expected: "unknown-location" }),
    ]),
  ]);
}

function problemDeviceRow(device) {
  const subtitle = [
    text(device.ip_address),
    `${text(device.vlan_name)} / ${text(device.vlan_id)}`,
  ].join(" / ");
  return el("button", {
    className: "problem-row",
    attrs: {
      type: "button",
      "data-device-focus": deviceKey(device),
      "data-device-vlan": device.vlan_id,
    },
  }, [
    statusDot(device.status),
    el("span", { className: "problem-main" }, [
      el("strong", { text: device.display_name || device.name || device.ip_address }),
      el("span", { text: subtitle }),
    ]),
    statusPill(device.status),
  ]);
}

function recentEvents(data) {
  return data.history?.events || [];
}

function eventKey(event) {
  if (event.id !== undefined && event.id !== null) {
    return `id:${event.id}`;
  }
  return [
    event.event_time,
    event.event_type,
    event.device_key,
    event.message,
  ].map(text).join("|");
}

function recentEventsPanel(data) {
  const events = recentEvents(data);
  const rows = events.length > 0
    ? events.map((event) => {
      const key = eventKey(event);
      const isNew = state.eventListInitialized && !state.seenEventKeys.has(key);
      return eventRow(event, isNew);
    })
    : [el("div", { className: "compact-empty", text: "No recent events" })];
  events.forEach((event) => state.seenEventKeys.add(eventKey(event)));
  state.eventListInitialized = true;
  return el("section", { className: "panel compact-panel" }, [
    sectionTitle("Recent Events"),
    el("div", { className: "event-list" }, rows),
  ]);
}

function eventRow(event, isNew = false) {
  const timestamp = formatEventTimestamp(event.event_time);
  return el("div", { className: `event-row ${statusClass(event.severity)} ${isNew ? "new-event" : ""}` }, [
    el("span", { className: "event-icon", html: icon(event.severity === "warning" ? "alert" : "history") }),
    el("span", { className: "event-message" }, [
      el("strong", { text: event.display_name || "Network event" }),
      el("span", { text: event.message || event.event_type || "Status changed" }),
    ]),
    el("span", { className: "event-meta", attrs: { title: timestamp } }, [
      el("span", { className: "event-time-ago", text: formatRelativeTime(event.event_time) }),
      el("span", { className: "event-timestamp", text: timestamp }),
    ]),
  ]);
}

function quickFilterButton(label, filters) {
  return el("button", {
    className: "quick-filter-button",
    text: label,
    attrs: {
      type: "button",
      "data-quick-filter": JSON.stringify(filters),
    },
  });
}

function overviewTopologyGraph(data) {
  const chain = topologyChain(data);
  const spine = el("div", { className: "topology-spine" });
  chain.forEach((node, index) => {
    spine.append(topologyGraphNode(node));
    if (index < chain.length - 1) {
      spine.append(topologyGraphConnector());
    }
  });

  return el("div", { className: "overview-topology-graph" }, [
    spine,
    el("div", { className: "topology-branch" }, [
      el("div", { className: "topology-vlan-grid" }, (data.devices_by_vlan || []).map((group) => topologyVlanNode(group, data))),
    ]),
  ]);
}

function topologyGraphConnector() {
  return el("span", { className: "topology-graph-connector", html: icon("arrow"), attrs: { "aria-hidden": "true" } });
}

function topologyGraphNode(node) {
  return el("article", { className: `topology-graph-node ${statusClass(node.status)}` }, [
    statusDot(node.status),
    el("span", { className: "topology-graph-icon", html: icon(node.icon) }),
    el("div", { className: "topology-graph-copy" }, [
      el("strong", { text: node.label }),
      el("span", { text: `${node.role} / ${text(node.ip)}` }),
    ]),
    node.item ? openWebAction(node.item) : el("span", { className: "graph-action-placeholder" }),
  ]);
}

function topologyVlanNode(group, data) {
  const status = vlanStatus(group, data);
  return el("button", {
    className: `topology-vlan-node ${statusClass(status)}`,
    attrs: { type: "button", "data-vlan-link": group.id, style: vlanStyle(groupVlanId(group)) },
  }, [
    statusDot(status),
    el("span", { className: "topology-vlan-icon", html: icon(vlanIconName(group.name)) }),
    el("span", { className: "topology-vlan-copy" }, [
      el("strong", { text: group.name }),
      el("span", { text: `VLAN ${group.id}` }),
    ]),
    el("span", { className: "topology-vlan-count", text: group.counts?.known ?? 0 }),
  ]);
}

function renderWarningStack(warnings) {
  const panel = el("section", { className: "warning-stack" });
  if (warnings.length === 0) {
    panel.append(el("div", { className: "notice online" }, [
      statusDot("online"),
      el("span", { text: "No warnings" }),
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
  const titleChildren = [el("h3", { text: title })];
  if (subtitle) {
    titleChildren.push(el("p", { text: subtitle }));
  }
  return el("div", { className: "section-title" }, [
    el("div", {}, titleChildren),
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

function configuredVlanColor(vlan, shade = "color") {
  const id = String(vlan ?? "");
  const palette = state.status?.ui?.vlan_colors || {};
  const entry = palette[id] || {};
  const fallback = {
    10: { color: "#0d8df2", dark: "#07579f", text: "#ffffff" },
    20: { color: "#00c822", dark: "#00750d", text: "#ffffff" },
    30: { color: "#df1726", dark: "#9b101a", text: "#ffffff" },
    40: { color: "#a52ab6", dark: "#681675", text: "#ffffff" },
    50: { color: "#ef790d", dark: "#844500", text: "#ffffff" },
    99: { color: "#f4f5f7", dark: "#cfd4da", text: "#101820" },
  }[Number(vlan)] || { color: "#27303a", dark: "#151a21", text: "#a8b3bf" };
  return entry[shade] || fallback[shade] || fallback.color;
}

function roleColor(role, shade = "color") {
  const key = normalize(role);
  const roles = state.status?.ui?.port_role_colors || {};
  const fallback = {
    stage: { color: "#f3d21b", dark: "#b79700", text: "#090909" },
    router: { color: "#050505", dark: "#000000", text: "#f3d21b" },
    trunk: { color: "#f3d21b", dark: "#b79700", text: "#090909" },
    unknown: { color: "#27303a", dark: "#151a21", text: "#a8b3bf" },
  };
  const roleKey = key.includes("router") ? "router" : key.includes("stage") || key.includes("downstream") ? "stage" : key.includes("trunk") ? "trunk" : "unknown";
  return (roles[roleKey] || fallback[roleKey] || fallback.unknown)[shade];
}

function vlanStyle(vlan) {
  return [
    `--vlan-color: ${configuredVlanColor(vlan, "color")}`,
    `--vlan-dark: ${configuredVlanColor(vlan, "dark")}`,
    `--vlan-text: ${configuredVlanColor(vlan, "text")}`,
  ].join("; ");
}

function groupVlanId(group) {
  return group?.id ?? group?.vlan_id ?? group?.vlan;
}

function vlanStatus(group, data) {
  const vlan = (data.vlans || []).find((item) => item.id === group.id);
  return vlan?.gateway_status || (group.counts?.offline ? "warning" : "online");
}

function vlanSummaryCard(group, data) {
  const status = vlanStatus(group, data);
  const card = el("button", {
    className: "vlan-summary-card",
    attrs: { type: "button", "data-vlan-link": group.id, style: vlanStyle(groupVlanId(group)) },
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
  const hasFilters = filtersActive();
  return el("section", { className: "filter-panel" }, [
    el("label", { className: `search-field ${state.filters.search ? "active-filter" : ""}` }, [
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
    el("button", {
      className: "clear-filters-button",
      text: "Clear filters",
      attrs: {
        type: "button",
        "data-clear-filters": "true",
        title: "Clear all filters",
        disabled: hasFilters ? null : "",
      },
    }),
  ]);
}

function selectField(label, id, value, options) {
  const active = String(value || "all") !== "all";
  const select = el("select", { attrs: { id } });
  options.forEach(([optionValue, optionLabel]) => {
    const option = el("option", { text: optionLabel, attrs: { value: optionValue } });
    if (String(optionValue) === String(value)) {
      option.selected = true;
    }
    select.append(option);
  });
  return el("label", { className: `select-field ${active ? "active-filter" : ""}` }, [
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
    attrs: {
      type: "button",
      "data-vlan-toggle": group.id,
      "aria-expanded": collapsed ? "false" : "true",
      title: collapsed ? "Expand VLAN group" : "Collapse VLAN group",
      style: vlanStyle(groupVlanId(group)),
    },
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
  const details = device.details || {};
  const row = el("details", {
    className: "device-row",
    attrs: { id: detailId, "data-device-key": key },
  });
  if (state.openDevices.has(key)) {
    row.open = true;
  }
  row.append(el("summary", { className: "device-summary" }, [
    el("span", { className: "device-expand-icon", html: icon("chevron"), attrs: { "aria-hidden": "true" } }),
    statusDot(device.status),
    el("div", { className: "device-primary" }, [
      el("strong", { text: device.display_name || device.name || device.ip_address }),
      el("span", { text: compactDeviceSubtitle(device) }),
    ]),
    el("span", { className: "device-vlan", text: `${text(device.vlan_name)} / ${text(device.vlan_id)}` }),
    badge(device.role || firstSource(device), device.expected ? "expected" : "neutral"),
    statusPill(device.status),
    el("span", { className: "details-hint", text: "Details" }),
    openWebAction(device),
  ]));
  row.append(el("div", { className: "device-details" }, [
    detailGroup("Identity", "chip", [
      detailItem("Configured", details.identity?.configured_name),
      detailItem("MAC", details.identity?.mac_address ?? device.mac_address),
      detailItem("Hostname", details.identity?.hostname ?? device.hostname),
      detailItem("DNS", details.identity?.dns_name),
      detailItem("Vendor", details.identity?.vendor ?? device.vendor),
      detailItem("Confidence", details.identity?.confidence),
    ]),
    detailGroup("Network", "network", [
      detailItem("IP", details.network?.ip_address ?? device.ip_address),
      detailItem("VLAN", details.network?.vlan ?? `${text(device.vlan_name)} / ${text(device.vlan_id)}`),
      detailItem("Subnet", details.network?.subnet ?? device.subnet),
      detailItem("Discovery", details.network?.discovery_sources ?? (device.discovery_sources || ["Unknown"]).join(", ")),
      detailItem("Latency", details.network?.latency),
      detailItem("Last check", formatDetailTime(details.network?.last_check)),
    ]),
    detailGroup("Location", "mapPin", [
      detailItem("Switch", details.location?.switch ?? device.connected_switch),
      detailItem("Port", details.location?.port ?? device.connected_port),
      detailItem("Port state", details.location?.port_state),
      detailItem("Confidence", details.location?.confidence ?? device.switch_port_confidence),
    ]),
    detailGroup("Services", "external", [
      detailItem("Web", details.services?.web_interface ?? device.web_url, webUrlFor(device)),
      detailItem("Source", details.services?.web_url_source ?? device.web_url_source),
      detailItem("Status check", details.services?.status_check),
    ]),
    detailGroup("History", "history", [
      detailItem("First seen", formatDetailTime(details.history?.first_seen ?? device.history?.first_seen)),
      detailItem("Last seen", formatDetailTime(details.history?.last_seen ?? device.last_seen)),
      detailItem("Status change", formatDetailTime(details.history?.last_status_change ?? device.history?.last_status_change)),
      detailItem("Offline since", formatDetailTime(details.history?.offline_since ?? device.history?.offline_since)),
      detailItem("Previous", details.history?.previous_status ?? device.history?.previous_status),
    ]),
    detailGroup("Notes", "devices", [
      detailItem("Owner", details.notes?.owner),
      detailItem("Criticality", details.notes?.criticality),
      detailItem("Asset", details.notes?.asset_tag),
      detailItem("Notes", details.notes?.notes),
    ]),
  ]));
  return row;
}

function compactDeviceSubtitle(device) {
  return [
    text(device.ip_address),
    isUnknown(device.hostname) ? "" : text(device.hostname),
    isUnknown(device.mac_address) ? "" : text(device.mac_address),
  ].filter(Boolean).join(" / ");
}

function firstSource(device) {
  return (device.discovery_sources || ["Unknown"])[0];
}

function formatDetailTime(value) {
  return isUnknown(value) ? "Unknown" : formatTime(value);
}

function detailGroup(title, iconName, items) {
  return el("section", { className: "detail-group" }, [
    el("h4", {}, [
      el("span", { html: icon(iconName), attrs: { "aria-hidden": "true" } }),
      el("span", { text: title }),
    ]),
    el("div", { className: "detail-grid" }, items),
  ]);
}

function detailItem(label, value, href = null) {
  const display = text(value);
  const valueNode = href && !isUnknown(display)
    ? el("a", { className: "value", text: display, attrs: { href, target: "_blank", rel: "noopener noreferrer" } })
    : unknownLabel(display);
  return el("div", { className: "detail-item" }, [
    el("span", { text: label }),
    valueNode,
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
    sectionTitle("VLANs"),
    el("div", { className: "vlan-lane-grid" }, (data.devices_by_vlan || []).map((group) => vlanLane(group, data))),
  ]);

  elements.pageContent.append(
    el("div", { className: "topology-layout" }, [
      path,
      el("div", { className: "topology-panels" }, [
        infrastructurePanel(data),
        switchPortMappingPanel(data),
        unmappedDevicesPanel(data),
        lanes,
      ]),
    ]),
  );
}

function topologyNode(node) {
  return el("article", { className: "topology-card" }, [
    el("span", { className: "topology-icon", html: icon(node.icon) }),
    el("div", { className: "topology-card-main" }, [
      el("strong", { text: node.label }),
      el("span", { text: `${node.role} / ${text(node.ip)}${node.item?.uptime ? ` / ${node.item.uptime}` : ""}` }),
    ]),
    node.item ? openWebAction(node.item) : el("span"),
    statusPill(node.status),
  ]);
}

function infrastructurePanel(data) {
  return el("section", { className: "panel" }, [
    sectionTitle("Infrastructure"),
    el("div", { className: "infra-detail-grid" }, (data.infrastructure || []).map(infrastructureDetailCard)),
  ]);
}

function infrastructureDetailCard(item) {
  const snmp = item.snmp || {};
  const ports = snmp.ports || [];
  const visiblePorts = ports.slice(0, 12);
  return el("article", { className: "infra-detail-card" }, [
    el("div", { className: "infra-detail-head" }, [
      el("span", { className: "topology-icon", html: icon(isRouter(item) ? "router" : "switch") }),
      el("div", {}, [
        el("strong", { text: item.name }),
        el("span", { text: `${text(item.ip_address)} / ${text(item.model)}` }),
      ]),
      statusPill(item.status),
    ]),
    el("div", { className: "infra-facts" }, [
      detailItem("Uptime", item.uptime),
      detailItem("SNMP name", item.system_name),
      detailItem("Description", item.system_description),
      detailItem("Ports", snmp.port_count !== undefined ? `${snmp.ports_up || 0}/${snmp.port_count} up` : "Unknown"),
    ]),
    visiblePorts.length > 0
      ? el("div", { className: "port-table" }, [
        el("div", { className: "port-table-head" }, [
          el("span", { text: "Port" }),
          el("span", { text: "State" }),
          el("span", { text: "Speed" }),
          el("span", { text: "Errors" }),
        ]),
        ...visiblePorts.map(portRow),
      ])
      : el("div", { className: "compact-empty", text: "No SNMP port data" }),
  ]);
}

function portRow(port) {
  const errors = Number(port.in_errors || 0) + Number(port.out_errors || 0);
  const speed = port.speed_mbps ? `${port.speed_mbps} Mbps` : "Unknown";
  return el("div", { className: "port-row" }, [
    el("span", { text: text(port.name) }),
    el("span", {}, [statusDot(port.oper_status === "up" ? "online" : port.oper_status === "down" ? "offline" : "unknown"), el("span", { text: text(port.oper_status) })]),
    el("span", { text: speed }),
    el("span", { text: errors ? String(errors) : "0" }),
  ]);
}

function switchPortMappingPanel(data) {
  const ports = data.topology?.ports || [];
  return el("section", { className: "panel" }, [
    sectionTitle("Known Port Locations"),
    ports.length
      ? el("div", { className: "port-location-grid" }, ports.map(portLocationCard))
      : el("div", { className: "compact-empty", text: "No proven switch-port locations yet" }),
  ]);
}

function portLocationCard(port) {
  return el("article", { className: "port-location-card" }, [
    el("div", { className: "port-location-head" }, [
      el("span", { html: icon("switch") }),
      el("strong", { text: `${text(port.switch)} / ${text(port.port)}` }),
      statusDot(port.port_state === "up" ? "online" : "unknown"),
    ]),
    el("div", { className: "port-device-list" }, (port.devices || []).map((device) => el("button", {
      className: "mini-device-row",
      attrs: {
        type: "button",
        "data-device-focus": deviceKey(device),
        "data-device-vlan": device.vlan_id,
      },
    }, [
      statusDot(device.status),
      el("span", {}, [
        el("strong", { text: device.display_name || device.name || device.ip_address }),
        el("span", { text: `${text(device.ip_address)} / ${text(device.vlan_name)}` }),
      ]),
    ]))),
  ]);
}

function unmappedDevicesPanel(data) {
  const devices = data.topology?.unmapped_devices || [];
  return el("section", { className: "panel" }, [
    sectionTitle("Unmapped Devices"),
    devices.length
      ? el("div", { className: "unmapped-device-grid" }, devices.slice(0, 24).map((device) => el("button", {
        className: "mini-device-row",
        attrs: {
          type: "button",
          "data-device-focus": deviceKey(device),
          "data-device-vlan": device.vlan_id,
        },
      }, [
        statusDot(device.status),
        el("span", {}, [
          el("strong", { text: device.display_name || device.name || device.ip_address }),
          el("span", { text: `${text(device.ip_address)} / ${text(device.vlan_name)}` }),
        ]),
      ])))
      : el("div", { className: "compact-empty", text: "All visible devices have a known location" }),
  ]);
}

function vlanLane(group, data) {
  const status = vlanStatus(group, data);
  return el("button", {
    className: "vlan-lane",
    attrs: { type: "button", "data-vlan-link": group.id, style: vlanStyle(groupVlanId(group)) },
  }, [
    el("span", { className: "vlan-icon", html: icon(vlanIconName(group.name)) }),
    el("div", {}, [
      el("strong", { text: `${group.name} / VLAN ${group.id}` }),
      el("span", { text: `${group.subnet} / ${group.counts.known} devices` }),
    ]),
    statusDot(status),
  ]);
}

function renderSwitchesPage(data) {
  const switches = data.switches || [];
  if (switches.length === 0) {
    elements.pageContent.append(emptyState("No switch layouts configured."));
    return;
  }

  elements.pageContent.append(
    vlanLegend(data),
    el("section", { className: "switch-page-grid" }, switches.map(switchFaceplate)),
  );
}

function vlanLegend(data) {
  const vlans = data.vlans || [];
  return el("section", { className: "vlan-legend" }, vlans.map((vlan) => el("button", {
    className: "vlan-legend-item",
    attrs: {
      type: "button",
      "data-vlan-link": vlan.id,
      style: vlanStyle(vlan.id),
      title: `${vlan.name} / VLAN ${vlan.id}`,
    },
  }, [
    el("span", { className: "legend-swatch" }),
    el("span", {}, [
      el("strong", { text: vlan.name }),
      el("small", { text: `VLAN ${vlan.id}` }),
    ]),
  ])));
}

function switchFaceplate(switchItem) {
  if (isAlliedGs95048(switchItem)) {
    return alliedGs950Faceplate(switchItem);
  }
  if (isTpLinkSg1016(switchItem)) {
    return tpLinkSg1016Faceplate(switchItem);
  }

  const ports = switchItem.ports || [];
  const columns = Number(switchItem.layout?.columns || (switchItem.port_count > 24 ? 24 : 8));
  const portGrid = el("div", { className: "switch-port-grid" }, ports.map((port) => portButton(switchItem, port)));
  portGrid.style.setProperty("--port-columns", columns);

  return el("article", { className: "switch-faceplate" }, [
    el("div", { className: "switch-faceplate-head" }, [
      el("span", { className: "topology-icon", html: icon("switch") }),
      el("div", {}, [
        el("strong", { text: switchItem.name }),
        el("span", { text: `${text(switchItem.model)} / ${text(switchItem.ip_address)}` }),
      ]),
      el("div", { className: "switch-faceplate-meta" }, [
        statusPill(switchItem.status),
        badge(`${switchItem.ports?.length || 0} ports`, "neutral"),
      ]),
    ]),
    el("div", { className: "switch-system-line" }, [
      el("span", { text: `SNMP ${text(switchItem.snmp_status)}` }),
      el("span", { text: `Uptime ${text(switchItem.uptime)}` }),
      el("span", { text: text(switchItem.sys_descr) }),
    ]),
    portGrid,
    ...switchPortInsight(switchItem),
  ]);
}

function isAlliedGs95048(switchItem) {
  return String(switchItem.id) === "foh-switch" || normalize(switchItem.model).includes("at-gs950/48");
}

function isTpLinkSg1016(switchItem) {
  const model = normalize(switchItem.model);
  return String(switchItem.id) === "stage-switch" || model.includes("tl-sg1016") || model.includes("sg1016");
}

function alliedGs950Faceplate(switchItem) {
  const portMap = portsByNumber(switchItem);
  return el("article", { className: "switch-faceplate allied-faceplate" }, [
    el("div", { className: "allied-panel" }, [
      el("div", { className: "allied-left-panel" }, [
        el("div", { className: "allied-brand-row" }, [
          el("strong", { text: "Allied Telesis" }),
          el("span", { text: "AT-GS950/48 Gigabit Ethernet Switch" }),
        ]),
        el("div", { className: "allied-led-area" }, [
          el("span", { text: "PORT ACTIVITY" }),
          el("span", { text: "LINK  ACT" }),
          el("span", { className: "eco-led", text: "ECO" }),
          el("i", { className: "panel-led on" }),
          el("small", { text: "SYSTEM" }),
        ]),
      ]),
      el("div", { className: "allied-copper-area" }, [
        alliedPortBlock(switchItem, portMap, [1, 3, 5, 7, 9, 11], [2, 4, 6, 8, 10, 12]),
        alliedPortBlock(switchItem, portMap, [13, 15, 17, 19, 21, 23], [14, 16, 18, 20, 22, 24]),
        alliedPortBlock(switchItem, portMap, [25, 27, 29, 31, 33, 35], [26, 28, 30, 32, 34, 36]),
        alliedPortBlock(
          switchItem,
          portMap,
          [37, 39, 41, 43, 45, 47],
          [38, 40, 42, 44, 46, 48],
          { comboCopper: true },
        ),
      ]),
      el("div", { className: "allied-sfp-area" }, [
        el("span", { className: "sfp-label", text: "SFP" }),
        el("div", { className: "sfp-grid" }, [
          portButton(switchItem, portMap.get("45"), { displayNumber: "45", media: "sfp" }),
          portButton(switchItem, portMap.get("47"), { displayNumber: "47", media: "sfp" }),
          portButton(switchItem, portMap.get("46"), { displayNumber: "46", media: "sfp" }),
          portButton(switchItem, portMap.get("48"), { displayNumber: "48", media: "sfp" }),
        ]),
      ]),
    ]),
    el("div", { className: "switch-system-line" }, [
      el("span", { text: `SNMP ${text(switchItem.snmp_status)}` }),
      el("span", { text: `Uptime ${text(switchItem.uptime)}` }),
      el("span", { text: text(switchItem.sys_descr) }),
    ]),
    ...switchPortInsight(switchItem),
  ]);
}

function tpLinkSg1016Faceplate(switchItem) {
  const portMap = portsByNumber(switchItem);
  return el("article", { className: "switch-faceplate tplink-faceplate" }, [
    el("div", { className: "tplink-panel" }, [
      el("div", { className: "tplink-left-panel" }, [
        el("div", { className: "tplink-brand-row" }, [
          el("strong", { text: "TP-Link" }),
          el("span", { text: "TL-SG1016PE" }),
        ]),
        el("div", { className: "tplink-led-area" }, [
          el("span", { text: "PORT STATUS" }),
          el("span", { text: "LINK / ACT" }),
          el("i", { className: "panel-led on" }),
          el("small", { text: "SYSTEM" }),
        ]),
      ]),
      el("div", { className: "tplink-port-area" }, [
        tpLinkPortBlock(switchItem, portMap, [1, 3, 5, 7], [2, 4, 6, 8]),
        tpLinkPortBlock(switchItem, portMap, [9, 11, 13, 15], [10, 12, 14, 16]),
      ]),
    ]),
    el("div", { className: "switch-system-line" }, [
      el("span", { text: `SNMP ${text(switchItem.snmp_status)}` }),
      el("span", { text: text(switchItem.sys_descr || switchItem.model) }),
    ]),
    ...switchPortInsight(switchItem),
  ]);
}

function portsByNumber(switchItem) {
  return new Map((switchItem.ports || []).map((port) => [String(port.number), port]));
}

function alliedPortBlock(switchItem, portMap, topPorts, bottomPorts, options = {}) {
  const copperLabel = (number) => options.comboCopper && number >= 45 ? `${number}R` : String(number);
  return el("div", { className: "allied-port-block" }, [
    el("div", { className: "allied-port-row top" }, topPorts.map((number) => portButton(switchItem, portMap.get(String(number)), {
      displayNumber: copperLabel(number),
      media: "copper",
    }))),
    el("div", { className: "allied-port-row bottom" }, bottomPorts.map((number) => portButton(switchItem, portMap.get(String(number)), {
      displayNumber: copperLabel(number),
      media: "copper",
    }))),
  ]);
}

function tpLinkPortBlock(switchItem, portMap, topPorts, bottomPorts) {
  return el("div", { className: "tplink-port-block" }, [
    el("div", { className: "tplink-port-row top" }, topPorts.map((number) => portButton(switchItem, portMap.get(String(number)), {
      displayNumber: number,
      media: "copper",
    }))),
    el("div", { className: "tplink-port-row bottom" }, bottomPorts.map((number) => portButton(switchItem, portMap.get(String(number)), {
      displayNumber: number,
      media: "copper",
    }))),
  ]);
}

function portButton(switchItem, port, options = {}) {
  if (!port) {
    return el("span", { className: "switch-port missing-port" });
  }
  const key = switchPortKey(switchItem.id, port.number);
  const selected = selectedPortKeyForSwitch(switchItem.id) === key;
  const displayNumber = options.displayNumber || port.number;
  const classes = [
    "switch-port",
    options.media ? `media-${options.media}` : "",
    `type-${normalize(port.type).replaceAll(" ", "-")}`,
    port.link_state === "up" ? "link-up" : port.link_state === "down" ? "link-down" : "link-unknown",
    port.learned_mac_count > 0 ? "has-macs" : "",
    selected ? "selected" : "",
  ].filter(Boolean).join(" ");
  return el("button", {
    className: classes,
    attrs: {
      type: "button",
      "data-switch-port": key,
      "data-port-tooltip": portTooltip(switchItem, port),
      style: portStyle(port),
    },
  }, [
    el("span", { className: "port-number", text: displayNumber }),
    el("span", { className: "port-jack", attrs: { "aria-hidden": "true" } }, [
      el("span", { className: "jack-notch" }),
      el("span", { className: "jack-cavity" }),
      el("span", { className: "jack-pins" }),
      el("span", { className: "port-led link-led" }),
      el("span", { className: "port-led activity-led" }),
    ]),
    el("span", { className: "port-label", text: isUnknown(port.label) ? port.type : port.label }),
    port.expected_vlans?.length
      ? el("span", { className: "port-vlan-strip" }, port.expected_vlans.slice(0, 6).map((vlan) => el("i", {
        attrs: { style: `--vlan-color: ${configuredVlanColor(vlan, "color")}` },
      })))
      : el("span"),
  ]);
}

function portStyle(port) {
  const color = portColor(port);
  return [
    `--port-color: ${color.color}`,
    `--port-dark: ${color.dark}`,
    `--port-text: ${color.text}`,
  ].join("; ");
}

function portColor(port) {
  const number = Number(port?.number);
  if (number === 47 || normalize(port?.role).includes("router")) {
    return {
      color: roleColor("router", "color"),
      dark: roleColor("router", "dark"),
      text: roleColor("router", "text"),
    };
  }
  if ((number >= 41 && number <= 46) || normalize(port?.type).includes("downstream") || normalize(port?.role).includes("stage")) {
    return {
      color: roleColor("stage", "color"),
      dark: roleColor("stage", "dark"),
      text: roleColor("stage", "text"),
    };
  }
  if (normalize(port?.type).includes("trunk") || normalize(port?.role).includes("uplink")) {
    return {
      color: roleColor("trunk", "color"),
      dark: roleColor("trunk", "dark"),
      text: roleColor("trunk", "text"),
    };
  }
  const vlan = (port?.untagged_vlans || [])[0] || port?.pvid || (port?.expected_vlans || [])[0];
  return {
    color: configuredVlanColor(vlan, "color"),
    dark: configuredVlanColor(vlan, "dark"),
    text: configuredVlanColor(vlan, "text"),
  };
}

function switchPortKey(switchId, portNumber) {
  return `${switchId}:${portNumber}`;
}

function selectedPortKeyForSwitch(switchId) {
  return state.selectedPorts?.[String(switchId)] || null;
}

function selectedSwitchPortForSwitch(switchItem) {
  const key = selectedPortKeyForSwitch(switchItem.id);
  if (!key) {
    return null;
  }
  const [switchId, portNumber] = key.split(":");
  if (String(switchItem.id) !== switchId) {
    return null;
  }
  const port = (switchItem.ports || []).find((item) => String(item.number) === String(portNumber));
  return port ? { switchItem, port } : null;
}

function switchPortInsight(switchItem) {
  const selected = selectedSwitchPortForSwitch(switchItem);
  return selected ? [portDetailPanel(selected)] : [];
}

function portTooltip(switchItem, port) {
  return [
    `${switchItem.name} / Port ${port.number}`,
    `State: ${text(port.link_state)}`,
    `Speed: ${port.speed_mbps ? `${port.speed_mbps} Mbps` : "Unknown"}`,
    `Role: ${text(port.role)}`,
    `VLANs: ${port.expected_vlans?.length ? port.expected_vlans.join(", ") : "Unknown"}`,
    `MACs: ${port.learned_mac_count || 0}`,
  ].join("\n");
}

function portDetailPanel(selected) {
  if (!selected) {
    return el("aside", { className: "port-detail-panel empty" }, [
      el("div", { className: "port-detail-empty" }, [
        el("span", { html: icon("switch") }),
        el("strong", { text: "Select a port" }),
      ]),
    ]);
  }

  const { switchItem, port } = selected;
  return el("aside", { className: "port-detail-panel" }, [
    el("div", { className: "port-detail-head" }, [
      el("div", {}, [
        el("span", { className: "summary-label", text: switchItem.name }),
        el("h3", { text: `Port ${port.number}` }),
      ]),
      el("div", { className: "port-detail-actions" }, [
        statusPill(port.link_state === "up" ? "online" : port.link_state === "down" ? "offline" : "unknown"),
        el("button", {
          className: "icon-button compact-icon-button",
          attrs: {
            type: "button",
            "data-close-switch-port": switchItem.id,
            "aria-label": "Collapse port details",
          },
        }, [
          el("span", { html: icon("chevron") }),
        ]),
      ]),
    ]),
    el("div", { className: "port-detail-groups" }, [
      detailGroup("Port", "switch", [
        detailItem("Label", port.label),
        detailItem("Role", port.role),
        detailItem("Type", port.type),
        detailItem("Notes", port.notes),
      ]),
      detailGroup("Link", "network", [
        detailItem("State", port.link_state),
        detailItem("Admin", port.admin_state),
        detailItem("Speed", port.speed_mbps ? `${port.speed_mbps} Mbps` : "Unknown"),
        detailItem("Source", port.source),
      ]),
      detailGroup("VLAN", vlanIconName(String(port.expected_vlans?.[0] || "")), [
        detailItem("Membership", port.expected_vlans?.length ? port.expected_vlans.join(", ") : "Unknown"),
        detailItem("Tagged", port.tagged_vlans?.length ? port.tagged_vlans.join(", ") : "None"),
        detailItem("Untagged", port.untagged_vlans?.length ? port.untagged_vlans.join(", ") : "None"),
        detailItem("Native", port.native_vlan),
        detailItem("PVID", port.pvid),
        detailItem("VLAN source", port.vlan_source),
        detailItem("Mode", port.uplink ? "uplink/trunk" : port.type),
      ]),
      detailGroup("Traffic", "history", [
        detailItem("RX bytes", formatBytes(port.in_octets)),
        detailItem("TX bytes", formatBytes(port.out_octets)),
        detailItem("RX errors", port.in_errors ?? "Unknown"),
        detailItem("TX errors", port.out_errors ?? "Unknown"),
      ]),
    ]),
    macLearningPanel(port),
    mappedDevicesPanel(port),
  ]);
}

function macLearningPanel(port) {
  const groups = port.learned_macs_by_vlan || [];
  return el("section", { className: "port-mac-panel" }, [
    sectionTitle("Learned MACs"),
    groups.length
      ? el("div", { className: "mac-vlan-groups" }, groups.map(macVlanGroup))
      : el("div", { className: "compact-empty", text: "No learned MACs" }),
  ]);
}

function macVlanGroup(group) {
  return el("div", { className: "mac-vlan-group" }, [
    el("div", { className: "mac-vlan-title" }, [
      el("span", { text: `${text(group.vlan_name)} / VLAN ${text(group.vlan_id)}` }),
      badge(`${group.macs?.length || 0}`, "neutral"),
    ]),
    el("div", { className: "mac-list" }, (group.macs || []).map((entry) => el("div", { className: `mac-row ${entry.direct ? "direct" : "learned-via-trunk"}` }, [
      el("span", { text: entry.mac_address }),
      el("span", { text: isUnknown(entry.device) ? "Unknown device" : `${entry.device} / ${text(entry.ip_address)}` }),
      el("span", { text: entry.direct ? "direct" : "learned" }),
    ]))),
  ]);
}

function mappedDevicesPanel(port) {
  const devices = port.mapped_devices || [];
  return el("section", { className: "port-mac-panel" }, [
    sectionTitle("Mapped Devices"),
    devices.length
      ? el("div", { className: "port-device-list" }, devices.map((device) => el("button", {
        className: "mini-device-row",
        attrs: {
          type: "button",
          "data-device-focus": device.ip_address || device.device || device.mac_address,
          "data-device-vlan": device.vlan_id || "all",
        },
      }, [
        statusDot("online"),
        el("span", {}, [
          el("strong", { text: text(device.device) }),
          el("span", { text: `${text(device.ip_address)} / ${text(device.mac_address)}` }),
        ]),
      ])))
      : el("div", { className: "compact-empty", text: "No directly mapped devices" }),
  ]);
}

function formatBytes(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "Unknown";
  }
  if (number < 1024) return `${number} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let prepared = number / 1024;
  let index = 0;
  while (prepared >= 1024 && index < units.length - 1) {
    prepared /= 1024;
    index += 1;
  }
  return `${prepared.toFixed(prepared >= 100 ? 0 : 1)} ${units[index]}`;
}

function vlanColor(vlan) {
  return configuredVlanColor(vlan, "color");
}

function renderSettings(data) {
  const discovery = data.discovery || {};
  const snmp = data.snmp || {};
  elements.pageContent.append(
    el("section", { className: "settings-grid" }, [
      settingsCard("Appearance", [
        segmentedSetting("Theme", "theme", state.settings.theme, [["system", "System"], ["dark", "Dark"], ["light", "Light"]]),
        toggleSetting("Compact mode", "compactMode", state.settings.compactMode),
      ]),
      settingsCard("Refresh", [
        segmentedSetting("Interval", "refreshInterval", String(state.settings.refreshInterval), [["5000", "5s"], ["10000", "10s"], ["30000", "30s"], ["60000", "60s"]]),
      ]),
      settingsCard("Discovery Display", [
        toggleSetting("Show unknown devices", "showUnknownDevices", state.settings.showUnknownDevices),
        toggleSetting("Show offline devices", "showOfflineDevices", state.settings.showOfflineDevices),
      ]),
      settingsCard("Runtime", [
        readonlyLine("App version", APP_VERSION),
        readonlyLine("Discovery", `${text(discovery.status)} / ${(discovery.subnets || []).length} subnets`),
        readonlyLine("History DB", data.history?.database_path),
        readonlyLine("SNMP", `${text(snmp.status)} / ${snmp.mac_observation_count || 0} MAC observations`),
        readonlyLine("Switches", `${(data.switches || []).length} layouts`),
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

function toggleSetting(label, key, checked) {
  const input = el("input", { attrs: { type: "checkbox", "data-toggle-setting": key } });
  input.checked = Boolean(checked);
  return el("label", { className: "toggle-row" }, [
    el("span", {}, [
      el("strong", { text: label }),
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
  } else if (state.route === "switches") {
    renderSwitchesPage(state.status);
  } else if (state.route === "settings") {
    renderSettings(state.status);
  } else {
    renderOverview(state.status);
  }
}

function captureStableScroll() {
  if (!["devices", "switches"].includes(state.route)) {
    return null;
  }
  return { left: window.scrollX, top: window.scrollY };
}

function restoreStableScroll(position) {
  if (!position) {
    return;
  }
  window.requestAnimationFrame(() => {
    window.scrollTo(position.left, position.top);
    window.requestAnimationFrame(() => {
      window.scrollTo(position.left, position.top);
    });
  });
}

async function loadStatus(options = {}) {
  if (state.loading) {
    return;
  }

  const scrollPosition = options.preserveScroll === false ? null : captureStableScroll();
  if (state.refreshTimer) {
    window.clearTimeout(state.refreshTimer);
    state.refreshTimer = null;
  }
  state.nextRefreshAt = Date.now();
  state.loading = true;
  updateStatusBar();
  elements.refreshButton.disabled = true;
  if (!state.status) {
    render();
  }

  try {
    const url = options.forceRefresh ? "/api/status?refresh=true" : "/api/status";
    const response = await fetch(url, { cache: "no-store" });
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
    restoreStableScroll(scrollPosition);
    scheduleNextRefresh();
  }
}

function renderPreservingScroll() {
  const position = captureStableScroll();
  render();
  restoreStableScroll(position);
}

function ensurePortTooltip() {
  if (!elements.portTooltip) {
    elements.portTooltip = el("div", { className: "port-hover-tooltip", attrs: { role: "tooltip" } });
    document.body.appendChild(elements.portTooltip);
  }
  return elements.portTooltip;
}

function positionPortTooltip(event) {
  if (!elements.portTooltip || !elements.portTooltip.classList.contains("visible")) {
    return;
  }
  const offset = 12;
  const rect = elements.portTooltip.getBoundingClientRect();
  let left = event.clientX + offset;
  let top = event.clientY + offset;
  if (left + rect.width > window.innerWidth - 8) {
    left = event.clientX - rect.width - offset;
  }
  if (top + rect.height > window.innerHeight - 8) {
    top = event.clientY - rect.height - offset;
  }
  elements.portTooltip.style.left = `${Math.max(8, left)}px`;
  elements.portTooltip.style.top = `${Math.max(8, top)}px`;
}

function showPortTooltip(target, event) {
  const value = target.dataset.portTooltip;
  if (!value) {
    return;
  }
  const tooltip = ensurePortTooltip();
  tooltip.replaceChildren(...value.split("\n").map((line, index) => {
    const tag = index === 0 ? "strong" : "span";
    return el(tag, { text: line });
  }));
  tooltip.classList.add("visible");
  positionPortTooltip(event);
}

function hidePortTooltip() {
  if (elements.portTooltip) {
    elements.portTooltip.classList.remove("visible");
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
  elements.refreshButton.addEventListener("click", () => loadStatus({ preserveScroll: true, forceRefresh: true }));

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

    const clearFiltersButton = event.target.closest("[data-clear-filters]");
    if (clearFiltersButton) {
      clearFilters();
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

    const quickFilter = event.target.closest("[data-quick-filter]");
    if (quickFilter) {
      try {
        state.filters = { ...defaultFilters(), ...JSON.parse(quickFilter.dataset.quickFilter || "{}") };
      } catch {
        state.filters = defaultFilters();
      }
      saveFilters();
      setRoute("devices");
      return;
    }

    const deviceFocus = event.target.closest("[data-device-focus]");
    if (deviceFocus) {
      state.filters = { ...defaultFilters(), vlan: String(deviceFocus.dataset.deviceVlan || "all") };
      state.openDevices.add(String(deviceFocus.dataset.deviceFocus));
      saveFilters();
      saveOpenDevices();
      setRoute("devices");
      return;
    }

    const closeSwitchPort = event.target.closest("[data-close-switch-port]");
    if (closeSwitchPort) {
      delete state.selectedPorts[String(closeSwitchPort.dataset.closeSwitchPort)];
      hidePortTooltip();
      renderPreservingScroll();
      return;
    }

    const switchPort = event.target.closest("[data-switch-port]");
    if (switchPort) {
      const [switchId] = switchPort.dataset.switchPort.split(":");
      if (state.selectedPorts[String(switchId)] === switchPort.dataset.switchPort) {
        delete state.selectedPorts[String(switchId)];
      } else {
        state.selectedPorts[String(switchId)] = switchPort.dataset.switchPort;
      }
      hidePortTooltip();
      renderPreservingScroll();
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

  elements.pageContent.addEventListener("mouseover", (event) => {
    const switchPort = event.target.closest("[data-port-tooltip]");
    if (switchPort) {
      showPortTooltip(switchPort, event);
    }
  });

  elements.pageContent.addEventListener("mousemove", (event) => {
    if (event.target.closest("[data-port-tooltip]")) {
      positionPortTooltip(event);
    }
  });

  elements.pageContent.addEventListener("mouseout", (event) => {
    const switchPort = event.target.closest("[data-port-tooltip]");
    if (switchPort && !switchPort.contains(event.relatedTarget)) {
      hidePortTooltip();
    }
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
  updateClockTimer();
  updateRefreshTimer();
  render();
  loadStatus();
}

init();

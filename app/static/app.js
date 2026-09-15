/* ABOUTUS Network Monitor – dashboard client.
 *
 * The browser never scans anything. It loads /api/state once, subscribes to
 * /api/events (SSE) and re-renders when the backend publishes updates. If SSE
 * is unavailable it falls back to passive polling of /api/state.
 */
"use strict";

const APP_VERSION = "1.0.0";
const UI_KEY = "aboutus-monitor-ui-v1";

// ----------------------------------------------------------------------------- state
const S = {
  state: null,
  scan: null,
  connection: "connecting",
  lastEventAt: null,
  route: { page: "overview", id: null },
  ui: loadUi(),
  drawer: null,
  es: null,
  esRetries: 0,
  pollTimer: null,
  toastSeq: 0,
};

function loadUi() {
  const defaults = { deviceFilters: { q: "", states: ["active_located"], vlan: "", switch: "", port: "", showInfra: false, showIgnored: false, favorites: false }, selectedPort: {}, problemFilter: "all", showAcked: false, settingsOpen: {} };
  try {
    const stored = JSON.parse(localStorage.getItem(UI_KEY) || "{}");
    return { ...defaults, ...stored, deviceFilters: { ...defaults.deviceFilters, ...(stored.deviceFilters || {}) } };
  } catch (_err) {
    return defaults;
  }
}
function saveUi() {
  try { localStorage.setItem(UI_KEY, JSON.stringify(S.ui)); } catch (_err) { /* ignore */ }
}

// ----------------------------------------------------------------------------- helpers
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "dataset") Object.entries(value).forEach(([k, v]) => { if (v !== undefined && v !== null) el.dataset[k] = v; });
    else if (key === "html") el.innerHTML = value;
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}
function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "dataset") Object.entries(value).forEach(([k, v]) => { if (v !== undefined && v !== null) el.dataset[k] = v; });
    else el.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
function text(value, fallback = "—") { return value === null || value === undefined || value === "" ? fallback : String(value); }
function fmtTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtDateTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtAgo(value) {
  if (!value) return "never";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const seconds = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
function fmtBps(value) {
  if (value === null || value === undefined) return "—";
  const units = ["b/s", "kb/s", "Mb/s", "Gb/s"];
  let v = Number(value);
  let i = 0;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}
function fmtSpeed(mbps) {
  if (!mbps) return "—";
  if (mbps >= 1000) return `${mbps / 1000} G`;
  return `${mbps} M`;
}
function fmtMac(mac) { return mac ? mac.toUpperCase() : "—"; }
function toneForStatus(status) {
  return { online: "ok", ok: "ok", offline: "crit", down: "crit", stale: "warn", unknown: "neutral", disabled: "neutral", warning: "warn", partial: "warn", error: "crit", pending: "neutral", skipped: "neutral", missing_tools: "crit" }[status] || "neutral";
}
function colorFor(name) {
  const colors = (S.state && S.state.colors) || {};
  return colors[name] || colors.unused || { color: "#2a3140", text: "#8b95a7" };
}
function vlanColorName(vlanId) {
  const vlan = (S.state?.vlans || []).find((v) => String(v.id) === String(vlanId));
  return vlan ? vlan.color : "unused";
}
function vlanName(vlanId) {
  const vlan = (S.state?.vlans || []).find((v) => String(v.id) === String(vlanId));
  return vlan ? vlan.name : `VLAN ${vlanId}`;
}
function vlanTag(vlanId, tagged = false) {
  const c = colorFor(vlanColorName(vlanId));
  return h("span", { class: "vlan-tag", dataset: { tagged: tagged ? "true" : "false" }, style: tagged ? { borderColor: c.color, color: c.color } : { background: c.color, color: c.text || "#fff" }, title: `${vlanName(vlanId)} (${tagged ? "tagged" : "untagged"})` }, String(vlanId));
}
function pill(label, tone = "neutral", title) { return h("span", { class: "pill", dataset: { tone }, title }, label); }
function statePill(state) {
  const labels = { active_located: "Located", active_unlocated: "Unlocated", relocating: "Relocating", stale: "Stale", offline: "Offline", mac_only: "MAC only" };
  return h("span", { class: "state-pill", dataset: { state } }, labels[state] || state);
}
function statusDot(status) { return h("span", { class: "status-dot", dataset: { status } }); }
function toast(message, tone = "neutral", timeout = 5000) {
  const host = document.getElementById("toasts");
  const el = h("div", { class: "toast", dataset: { tone } }, message);
  host.appendChild(el);
  setTimeout(() => el.remove(), timeout);
}
async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  let payload = null;
  try { payload = await response.json(); } catch (_err) { payload = null; }
  if (!response.ok) throw new Error((payload && (payload.error || payload.detail)) || `${response.status} ${response.statusText}`);
  return payload;
}
function switchById(id) { return (S.state?.switches || []).find((s) => s.id === id); }
function infraById(id) { return (S.state?.infrastructure || []).find((i) => i.id === id); }
function apById(id) { return (S.state?.access_points || []).find((i) => i.id === id); }
function deviceByKey(key) { return (S.state?.devices || []).find((d) => d.key === key) || (S.state?.device_observations?.mac_only || []).find((d) => d.key === key); }
function navigate(hash) { location.hash = hash; }

// ----------------------------------------------------------------------------- live connection
function applyEvent(type, data) {
  S.lastEventAt = Date.now();
  switch (type) {
    case "state_snapshot":
      S.state = data;
      S.scan = data.scan;
      break;
    case "device_update":
      if (!S.state) return;
      Object.assign(S.state, { generated_at: data.generated_at, devices: data.devices, device_observations: data.device_observations, device_counts: data.device_counts, access_points: data.access_points, vlans: data.vlans, infrastructure: data.infrastructure, internet: data.internet, history: data.history, discovery: data.discovery, snmp: data.snmp, cycle: data.cycle, mode: data.mode, cycle_duration_ms: data.cycle_duration_ms, errors: data.errors });
      break;
    case "switch_update":
      if (!S.state) return;
      S.state.switches = data.switches;
      break;
    case "topology_update":
      if (!S.state) return;
      S.state.topology = data.topology;
      break;
    case "problem_update":
      if (!S.state) return;
      S.state.problems = data.problems;
      S.state.problem_counts = data.problem_counts;
      break;
    case "port_update":
      (data.changes || []).forEach((change) => {
        const sw = infraById(change.switch_id);
        toast(`${sw ? sw.name : change.switch_id} port ${change.port} is now ${change.to}`, change.to === "up" ? "ok" : "warn");
      });
      return;
    case "scan_status":
      S.scan = data;
      renderStatusBar();
      return;
    case "heartbeat":
      if (data && data.scan) S.scan = data.scan;
      renderStatusBar();
      return;
    case "error":
      if (data && data.scan) S.scan = data.scan;
      toast(`Backend: ${data.message}`, "crit", 8000);
      renderStatusBar();
      return;
    default:
      return;
  }
  render();
}

function connectEvents() {
  if (S.es) { try { S.es.close(); } catch (_err) { /* ignore */ } }
  if (!("EventSource" in window)) { startPolling(); return; }
  S.connection = S.esRetries ? "reconnecting" : "connecting";
  renderStatusBar();
  const es = new EventSource("/api/events");
  S.es = es;
  const types = ["state_snapshot", "scan_status", "device_update", "switch_update", "port_update", "topology_update", "problem_update", "heartbeat", "error"];
  types.forEach((type) => es.addEventListener(type, (event) => {
    let data = {};
    try { data = JSON.parse(event.data); } catch (_err) { data = {}; }
    if (S.connection !== "live") { S.connection = "live"; S.esRetries = 0; stopPolling(); }
    applyEvent(type, data);
  }));
  es.onopen = () => { S.connection = "live"; S.esRetries = 0; stopPolling(); renderStatusBar(); };
  es.onerror = () => {
    es.close();
    S.esRetries += 1;
    S.connection = S.esRetries > 3 ? "polling" : "reconnecting";
    renderStatusBar();
    if (S.esRetries > 3) startPolling();
    setTimeout(connectEvents, Math.min(30000, 1000 * Math.pow(2, Math.min(5, S.esRetries))));
  };
}
function startPolling() {
  if (S.pollTimer) return;
  S.pollTimer = setInterval(fetchState, 15000);
}
function stopPolling() {
  if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
}
async function fetchState() {
  try {
    const data = await api("/api/state");
    S.state = data;
    S.scan = data.scan;
    S.lastEventAt = Date.now();
    if (S.connection === "offline") S.connection = S.es && S.es.readyState === 1 ? "live" : "polling";
    render();
  } catch (err) {
    S.connection = "offline";
    renderStatusBar();
  }
}

async function requestScan(mode) {
  const button = mode === "full" ? document.getElementById("btnFullScan") : document.getElementById("btnRefresh");
  button.disabled = true;
  try {
    const result = await api(`/api/scan/request?mode=${mode}`, { method: "POST" });
    S.scan = result.scan || S.scan;
    if (result.accepted) toast(result.message || `${mode} scan ${result.queued ? "queued" : "started"}`, "ok", 3000);
    else toast(result.message || "Scan not accepted", "warn", 4000);
    renderStatusBar();
  } catch (err) {
    toast(`Scan request failed: ${err.message}`, "crit");
  } finally {
    setTimeout(() => { button.disabled = false; renderStatusBar(); }, 1200);
  }
}

// ----------------------------------------------------------------------------- status bar
function renderStatusBar() {
  const live = document.getElementById("chipLive");
  const liveText = document.getElementById("chipLiveText");
  live.dataset.state = S.connection;
  liveText.textContent = { connecting: "Connecting…", live: "Live", reconnecting: "Reconnecting…", polling: "Polling (no live stream)", offline: "Backend unreachable" }[S.connection] || S.connection;

  const scan = S.scan || {};
  const spinner = document.getElementById("scanSpinner");
  const scanChip = document.getElementById("chipScan");
  const scanText = document.getElementById("chipScanText");
  const phases = { checks: "pinging", snmp: "polling SNMP", discovery: "discovering hosts", correlate: "correlating", analyze: "analysing", persist: "saving", queued: "starting" };
  if (scan.state === "running") {
    spinner.hidden = false;
    scanText.textContent = `${scan.mode === "full" ? "Full scan" : scan.mode === "targeted" ? "Targeted refresh" : "Fast poll"}: ${phases[scan.phase] || scan.phase}`;
    scanChip.dataset.level = "";
  } else {
    spinner.hidden = true;
    const parts = [];
    if (scan.last_success_at) parts.push(`last poll ${fmtAgo(scan.last_success_at)}`);
    if (scan.queued_mode) parts.push(`${scan.queued_mode} queued`);
    else if (scan.cooldown_remaining_seconds > 0) parts.push(`cooldown ${Math.ceil(scan.cooldown_remaining_seconds)}s`);
    else if (scan.next_fast_in_seconds !== undefined) parts.push(`next in ${Math.ceil(scan.next_fast_in_seconds)}s`);
    scanText.textContent = parts.length ? `Idle · ${parts.join(" · ")}` : "Idle";
    scanChip.dataset.level = scan.phase === "error" ? "warn" : "";
  }
  const age = document.getElementById("chipAge");
  const ageText = document.getElementById("chipAgeText");
  if (S.state && S.state.generated_at) {
    const seconds = Math.round((Date.now() - new Date(S.state.generated_at).getTime()) / 1000);
    ageText.textContent = `Data ${seconds < 5 ? "just now" : `${seconds}s old`}`;
    age.dataset.level = seconds > 180 ? "crit" : seconds > 60 ? "warn" : "";
  } else {
    ageText.textContent = S.state ? "Waiting for first poll…" : "No data yet";
    age.dataset.level = "";
  }
  const errorChip = document.getElementById("chipError");
  const lastError = scan.last_error || (S.state && S.state.config_error);
  errorChip.hidden = !lastError;
  document.getElementById("chipErrorText").textContent = lastError ? `Error: ${lastError}` : "";

  const full = document.getElementById("btnFullScan");
  const wait = Math.ceil(scan.full_scan_available_in_seconds || 0);
  full.textContent = wait > 0 ? `Full scan (${wait}s)` : "Full scan";
  full.title = wait > 0 ? `Full discovery is rate limited; available in ${wait}s. Requesting it now runs a fast poll instead.` : "Full discovery sweep of all monitored VLANs";

  const counts = S.state?.problem_counts || {};
  const badge = document.getElementById("navProblemBadge");
  const critical = counts.critical || 0;
  const warning = counts.warning || 0;
  badge.hidden = !(critical || warning);
  badge.textContent = critical || warning;
  badge.dataset.level = critical ? "critical" : "warning";
}
function tickClock() {
  document.getElementById("chipClock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  if (S.state) {
    const ageText = document.getElementById("chipAgeText");
    const seconds = S.state.generated_at ? Math.round((Date.now() - new Date(S.state.generated_at).getTime()) / 1000) : null;
    if (seconds !== null) {
      ageText.textContent = `Data ${seconds < 5 ? "just now" : `${seconds}s old`}`;
      document.getElementById("chipAge").dataset.level = seconds > 180 ? "crit" : seconds > 60 ? "warn" : "";
    }
    if (S.scan && S.scan.state !== "running") renderStatusBar();
  }
  if (S.connection === "live" && S.lastEventAt && Date.now() - S.lastEventAt > 60000) {
    S.connection = "reconnecting";
    connectEvents();
  }
}

// ----------------------------------------------------------------------------- routing
function parseRoute() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [page, id] = hash.split("/");
  const known = ["overview", "topology", "switches", "access-points", "devices", "problems", "settings"];
  S.route = { page: known.includes(page) ? page : "overview", id: id ? decodeURIComponent(id) : null };
}
function render() {
  parseRoute();
  document.querySelectorAll("[data-route]").forEach((a) => a.classList.toggle("active", a.dataset.route === S.route.page));
  renderStatusBar();
  const content = document.getElementById("content");
  const scrollY = window.scrollY;
  clear(content);
  if (!S.state) {
    content.appendChild(h("div", { class: "empty" }, S.connection === "offline" ? "The backend is not reachable. Check that the aboutus-network-monitor service is running." : "Connecting to the monitor backend…"));
    return;
  }
  if (!S.state.ready) {
    content.appendChild(h("div", { class: "empty" }, S.state.config_error ? `Configuration error: ${S.state.config_error}` : "First poll is running – data will appear in a few seconds."));
    if (S.route.page === "settings") content.appendChild(renderSettings());
    return;
  }
  const pages = { overview: renderOverview, topology: renderTopologyPage, switches: renderSwitchesPage, "access-points": renderAccessPointsPage, devices: renderDevicesPage, problems: renderProblemsPage, settings: renderSettings };
  content.appendChild(pages[S.route.page]());
  window.scrollTo(0, scrollY);
  if (S.drawer) refreshDrawer();
}

// ----------------------------------------------------------------------------- overview
function readiness() {
  const counts = S.state.problem_counts || {};
  const infraDown = (S.state.infrastructure || []).filter((i) => i.status === "offline" && !i.optional && !i.planned && i.enabled !== false);
  if (counts.critical || infraDown.length) return { tone: "crit", icon: "⛔", title: "Attention required", sub: `${counts.critical || 0} critical problem${counts.critical === 1 ? "" : "s"}${infraDown.length ? `, ${infraDown.length} core device${infraDown.length === 1 ? "" : "s"} offline` : ""}` };
  if (counts.warning) return { tone: "warn", icon: "⚠️", title: "Show ready with warnings", sub: `${counts.warning} warning${counts.warning === 1 ? "" : "s"} to review` };
  return { tone: "ok", icon: "✅", title: "Show ready", sub: "All core infrastructure online, no critical problems" };
}
function renderOverview() {
  const st = S.state;
  const counts = st.device_counts || {};
  const pc = st.problem_counts || {};
  const ready = readiness();
  const page = h("div");
  page.appendChild(h("div", { class: "readiness", dataset: { tone: ready.tone } }, h("span", { class: "icon" }, ready.icon), h("div", {}, h("div", { class: "big" }, ready.title), h("div", { class: "muted" }, ready.sub)), h("div", { class: "grow" }), h("div", { class: "dim small" }, `Cycle ${st.cycle} · ${st.mode} · ${st.cycle_duration_ms ? `${(st.cycle_duration_ms / 1000).toFixed(1)}s` : ""} · updated ${fmtTime(st.generated_at)}`)));

  page.appendChild(h("div", { class: "kpis" },
    kpi("Critical", pc.critical || 0, pc.critical ? "crit" : "ok", "problems", "#/problems"),
    kpi("Warnings", pc.warning || 0, pc.warning ? "warn" : "ok", "problems", "#/problems"),
    kpi("Devices located", counts.active_located || 0, "ok", `${counts.total || 0} known`, "#/devices"),
    kpi("Unlocated online", counts.active_unlocated || 0, counts.active_unlocated ? "warn" : "ok", "no proven port", "#/devices"),
    kpi("Unknown online", counts.unknown || 0, counts.unknown ? "warn" : "ok", "not named yet", "#/devices"),
    kpi("Offline / stale", (counts.offline || 0) + (counts.stale || 0) + (counts.relocating || 0), "info", "known devices", "#/devices"),
  ));

  const infra = st.infrastructure || [];
  page.appendChild(section("Infrastructure", h("div", { class: "grid grid-cards" }, infra.map((item) => item.kind === "ap" && apById(item.id) ? accessPointCard(apById(item.id), { compact: true }) : infraCard(item)))));

  page.appendChild(h("div", { class: "grid grid-2 section" },
    h("div", { class: "panel" }, h("div", { class: "panel-title" }, "Topology", h("span", { class: "spacer" }), h("a", { href: "#/topology", class: "small" }, "Open")), renderTopologySvg(st.topology, { compact: true })),
    h("div", { class: "panel" }, h("div", { class: "panel-title" }, "Problems", h("span", { class: "spacer" }), h("a", { href: "#/problems", class: "small" }, "All")), problemList((st.problems || []).filter((p) => !p.acknowledged_at).slice(0, 6), { compact: true })),
  ));

  page.appendChild(section("VLANs", h("div", { class: "grid grid-cards" }, (st.vlans || []).map(vlanCard))));

  page.appendChild(h("div", { class: "grid grid-2 section" },
    h("div", { class: "panel" }, h("div", { class: "panel-title" }, "Recent events"), eventList((st.history?.events || []).slice(0, 12))),
    h("div", { class: "panel" }, h("div", { class: "panel-title" }, "Monitor"), monitorPanel()),
  ));
  return page;
}
function kpi(label, value, tone, hint, href) {
  return h("div", { class: "kpi", dataset: { tone }, onclick: () => navigate(href) }, h("div", { class: "label" }, label), h("div", { class: "value" }, String(value)), h("div", { class: "hint" }, hint));
}
function section(title, body, count) {
  return h("div", { class: "section" }, h("div", { class: "section-title" }, h("h3", {}, title), count !== undefined ? h("span", { class: "count" }, String(count)) : null), body);
}
function infraCard(item) {
  const snmp = item.snmp || {};
  const kindLabel = { router: "Router", switch: "Switch", ap: "Access point", monitor: "Monitor" }[item.kind] || item.kind;
  const meta = [];
  if (item.ip_address) meta.push(h("span", { class: "mono" }, item.ip_address));
  if (item.model) meta.push(h("span", {}, item.model));
  if (snmp.uptime) meta.push(h("span", {}, `up ${snmp.uptime}`));
  if (item.ports_up !== undefined) meta.push(h("span", {}, `${item.ports_up} ports up`));
  if (item.check && item.check.latency_ms !== null && item.check.latency_ms !== undefined) meta.push(h("span", {}, `${item.check.latency_ms} ms`));
  const badges = [];
  if (item.planned) badges.push(pill("planned", "neutral"));
  else if (item.optional) badges.push(pill("optional", "neutral"));
  if (item.kind !== "monitor") badges.push(pill(`SNMP ${snmp.status || "—"}`, toneForStatus(snmp.status), snmp.error || ""));
  if (item.problem_count) badges.push(pill(`${item.problem_count} problem${item.problem_count === 1 ? "" : "s"}`, "crit"));
  return h("div", { class: "infra-card", dataset: { status: item.status, optional: String(!!item.optional), planned: String(!!item.planned) }, onclick: () => { if (item.kind === "switch") navigate(`#/switches/${encodeURIComponent(item.id)}`); else if (item.kind === "ap") navigate(`#/access-points/${encodeURIComponent(item.id)}`); else openDrawer({ type: "infra", id: item.id }); } },
    h("div", { class: "head" }, statusDot(item.status), h("span", { class: "name" }, item.name), h("span", { class: "dim small" }, kindLabel)),
    h("div", { class: "meta" }, meta),
    h("div", { class: "flex" }, badges),
  );
}
function accessPointCard(ap, { compact } = {}) {
  const uplink = ap.uplink || {};
  const warnings = ap.warnings || [];
  const warnCount = warnings.filter((w) => w.severity !== "info").length;
  const meta = [
    ap.ip_address ? h("span", { class: "mono" }, ap.ip_address) : null,
    ap.location ? h("span", {}, ap.location) : null,
    ap.model ? h("span", {}, ap.model) : null,
    ap.uptime ? h("span", {}, `up ${ap.uptime}`) : null,
  ].filter(Boolean);
  const uplinkText = uplink.expected ? `${uplink.switch_name || uplink.switch_id || "switch"} port ${text(uplink.port)}${uplink.port_label ? ` (${uplink.port_label})` : ""}` : "not planned";
  return h("div", { class: "infra-card ap-card", dataset: { status: ap.state, optional: String(!!ap.optional), planned: String(!!ap.planned) }, onclick: () => navigate(`#/access-points/${encodeURIComponent(ap.id)}`) },
    h("div", { class: "head" }, statusDot(ap.state), h("span", { class: "name" }, ap.name), h("span", { class: "dim small" }, "Access point")),
    h("div", { class: "meta" }, meta),
    h("div", { class: "ap-metrics" },
      h("span", {}, "Uplink ", h("b", {}, `${text(uplink.port_status || ap.ethernet?.oper_status)}${uplink.speed_mbps || ap.ethernet?.speed_mbps ? ` · ${fmtSpeed(uplink.speed_mbps || ap.ethernet?.speed_mbps)}` : ""}`)),
      h("span", {}, "Traffic ", h("b", {}, `${fmtBps(ap.traffic?.in_bps)} in / ${fmtBps(ap.traffic?.out_bps)} out`)),
      compact ? null : h("span", {}, "Clients ", h("b", {}, ap.client_count_available ? String(ap.client_count) : "n/a")),
    ),
    h("div", { class: "dim small" }, uplinkText, uplink.confirmation === "unconfirmed" ? " · unconfirmed" : ""),
    h("div", { class: "flex" },
      pill(ap.state, toneForStatus(ap.state)),
      pill(`SNMP ${ap.snmp_status || "—"}`, toneForStatus(ap.snmp_status), ap.last_error || ""),
      uplink.confirmation ? pill(uplink.confirmation, uplink.confirmation === "confirmed" ? "ok" : uplink.confirmation === "unconfirmed" ? "warn" : "neutral") : null,
      warnCount ? pill(`${warnCount} warning${warnCount === 1 ? "" : "s"}`, "warn") : warnings.length ? pill(`${warnings.length} note${warnings.length === 1 ? "" : "s"}`, "info") : pill("AP OK", "ok"),
    ),
  );
}
function vlanCard(vlan) {
  const c = colorFor(vlan.color);
  const dc = vlan.device_counts || {};
  return h("div", { class: "vlan-card", style: { "--vlan-color": c.color }, onclick: () => { S.ui.deviceFilters = { ...S.ui.deviceFilters, vlan: String(vlan.id), states: [] }; saveUi(); navigate("#/devices"); } },
    h("div", { class: "head" }, h("span", { class: "name" }, vlan.name), h("span", { class: "id" }, `VLAN ${vlan.id}`), h("span", { class: "grow" }), vlan.gateway ? pill(`GW ${vlan.gateway_status}`, toneForStatus(vlan.gateway_status), vlan.gateway) : pill("isolated", "neutral")),
    h("div", { class: "dim small mono" }, vlan.subnet || vlan.description || "no IP subnet"),
    h("div", { class: "counts" }, h("span", {}, h("b", {}, String(dc.active || 0)), " active"), h("span", {}, h("b", {}, String(dc.located || 0)), " located"), h("span", {}, h("b", {}, String((dc.offline || 0) + (dc.stale || 0))), " offline/stale")),
  );
}
function monitorPanel() {
  const st = S.state;
  const scan = S.scan || {};
  const disc = st.discovery || {};
  const items = [
    ["Backend version", st.version],
    ["Station", `${st.station?.hostname || "aboutus-net"} (${st.station?.ip_address || ""})`],
    ["Fast poll", `every ${scan.fast_interval_seconds}s · last ${fmtAgo(scan.last_success_at)}`],
    ["Full discovery", `every ${scan.full_interval_seconds}s · ${disc.tool || "?"} · last ${fmtAgo(scan.last_full_at)} · ${disc.host_count ?? 0} hosts`],
    ["SNMP", `${st.snmp?.status || "—"}${st.snmp?.errors?.length ? ` (${st.snmp.errors.length} errors)` : ""}`],
    ["Internet", `${st.internet?.status || "—"} · ${(st.internet?.probes || []).map((p) => `${p.name}: ${p.status}`).join(", ")}`],
    ["Last cycle", `${st.cycle_duration_ms ? `${(st.cycle_duration_ms / 1000).toFixed(1)}s` : "—"} (${st.mode})`],
  ];
  const errors = (st.errors || []).slice(0, 3);
  return h("div", {}, h("dl", { class: "kv" }, items.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])), errors.length ? h("div", { class: "list", style: { marginTop: "10px" } }, errors.map((e) => h("div", { class: "list-item" }, h("span", { class: "sev", dataset: { severity: "critical" } }), h("div", { class: "body" }, h("div", { class: "sub" }, e.message)), h("span", { class: "time" }, fmtTime(e.time))))) : null);
}
function eventList(events) {
  if (!events.length) return h("div", { class: "empty" }, "No events yet");
  return h("div", { class: "list event-list" }, events.map((e) => h("div", { class: "list-item" }, h("span", { class: "sev", dataset: { severity: e.severity } }), h("div", { class: "body" }, h("div", { class: "sub" }, e.message)), h("span", { class: "time" }, fmtTime(e.time)))));
}

// ----------------------------------------------------------------------------- topology
function renderTopologyPage() {
  const page = h("div");
  page.appendChild(h("div", { class: "page-head" }, h("h2", {}, "Topology"), h("span", { class: "sub" }, "Expected links from the plan, verified with LLDP and switch MAC tables")));
  page.appendChild(h("div", { class: "topology-wrap" }, renderTopologySvg(S.state.topology, { compact: false })));
  const links = (S.state.topology?.links || []).filter((l) => l.kind !== "wan");
  const rows = links.map((link) => {
    const a = infraById(link.source) || { name: link.source };
    const b = infraById(link.target) || { name: link.target };
    const vl = link.vlans || {};
    return h("tr", { onclick: () => navigate(`#/switches/${encodeURIComponent(switchById(link.source) ? link.source : link.target)}`) },
      h("td", {}, h("div", {}, a.name), h("div", { class: "dim small" }, `port ${text(link.source_port)} ${link.source_port_label ? `(${link.source_port_label})` : ""}`)),
      h("td", {}, h("div", {}, b.name), h("div", { class: "dim small" }, `port ${text(link.target_port)}`)),
      h("td", {}, pill(link.kind.replace("_", " "), link.kind === "trunk" ? "neutral" : "info")),
      h("td", {}, pill(link.status, toneForStatus(link.status === "up" ? "online" : link.status === "down" ? "offline" : link.status)), " ", link.speed_mbps ? h("span", { class: "dim small" }, fmtSpeed(link.speed_mbps)) : null),
      h("td", {}, (link.expected ? [pill((link.observed || []).length ? "planned" : "unconfirmed", (link.observed || []).length ? "neutral" : "warn")] : [pill("unplanned", "warn")]).concat((link.observed || []).map((o) => pill(o.toUpperCase(), "ok")))),
      h("td", { class: "hide-mobile" }, (vl.untagged || []).map((v) => vlanTag(v, false)), " ", (vl.tagged || []).map((v) => vlanTag(v, true))),
      h("td", {}, (link.issues || []).concat((link.port_issues || []).map((m) => ({ message: m, severity: "warning" }))).map((i) => h("div", { class: "small", style: { color: i.severity === "critical" ? "var(--crit)" : "var(--warn)" } }, i.message))),
    );
  });
  page.appendChild(section("Links", h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", {}, "From"), h("th", {}, "To"), h("th", {}, "Type"), h("th", {}, "Link"), h("th", {}, "Evidence"), h("th", { class: "hide-mobile" }, "VLANs"), h("th", {}, "Issues"))), h("tbody", {}, rows))), links.length));
  return page;
}
function renderTopologySvg(topology, { compact }) {
  const nodes = (topology?.nodes || []).filter((n) => !(n.kind === "ap" && n.planned && compact));
  const links = topology?.links || [];
  const levels = new Map();
  nodes.forEach((n) => { const d = n.depth ?? 3; if (!levels.has(d)) levels.set(d, []); levels.get(d).push(n); });
  const depths = [...levels.keys()].sort((a, b) => a - b);
  const width = Math.max(640, Math.max(...depths.map((d) => levels.get(d).length)) * (compact ? 170 : 210) + 40);
  const rowH = compact ? 92 : 120;
  const height = depths.length * rowH + 30;
  const pos = new Map();
  depths.forEach((d, row) => {
    const items = levels.get(d);
    const gap = width / (items.length + 1);
    items.forEach((n, i) => pos.set(n.id, { x: gap * (i + 1), y: 40 + row * rowH }));
  });
  const nodeW = compact ? 138 : 170;
  const nodeH = compact ? 44 : 52;
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Network topology" });
  const linkLayer = svg("g");
  const labelLayer = svg("g");
  links.forEach((link) => {
    const a = pos.get(link.source);
    const b = pos.get(link.target);
    if (!a || !b) return;
    const path = svg("path", { class: "topo-link", dataset: { health: link.health || "unknown", kind: link.kind }, d: `M${a.x},${a.y + nodeH / 2} C${a.x},${(a.y + b.y) / 2} ${b.x},${(a.y + b.y) / 2} ${b.x},${b.y - nodeH / 2}` });
    path.appendChild(svg("title", {}, `${link.source} ${text(link.source_port)} ↔ ${link.target} ${text(link.target_port)} · ${link.status} · ${(link.observed || []).join("+") || "not observed"}${link.issues?.length ? `\n${link.issues.map((i) => i.message).join("\n")}` : ""}`));
    linkLayer.appendChild(path);
    if (!compact && link.kind !== "wan") {
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      const label = `${text(link.source_port, "?")}↔${text(link.target_port, "?")}${link.speed_mbps ? ` ${fmtSpeed(link.speed_mbps)}` : ""}`;
      const w = label.length * 6.2 + 10;
      labelLayer.appendChild(svg("rect", { class: "topo-link-bg", x: mx - w / 2, y: my - 8, width: w, height: 16, rx: 4 }));
      labelLayer.appendChild(svg("text", { class: "topo-link-label", x: mx, y: my + 3.5, "text-anchor": "middle" }, label));
    }
  });
  root.appendChild(linkLayer);
  nodes.forEach((n) => {
    const p = pos.get(n.id);
    const g = svg("g", { class: `topo-node${n.is_core ? " core" : ""}`, dataset: { status: n.status, planned: String(!!n.planned) }, transform: `translate(${p.x - nodeW / 2},${p.y - nodeH / 2})`, onclick: () => { if (n.kind === "switch") navigate(`#/switches/${encodeURIComponent(n.id)}`); else if (n.kind === "ap") navigate(`#/access-points/${encodeURIComponent(n.id)}`); else if (n.kind !== "internet") openDrawer({ type: "infra", id: n.id }); } });
    g.appendChild(svg("rect", { width: nodeW, height: nodeH, rx: 9 }));
    g.appendChild(svg("circle", { cx: 14, cy: nodeH / 2, r: 5, fill: n.status === "online" ? "#22c55e" : n.status === "offline" ? "#ef4444" : "#6b7280" }));
    g.appendChild(svg("text", { x: 26, y: compact ? 19 : 22, "font-weight": "600" }, (n.name || n.id).slice(0, compact ? 18 : 24)));
    const sub = n.kind === "internet" ? (n.status || "") : [n.ip_address, n.kind === "switch" && n.active_ports !== undefined ? `${n.active_ports}/${n.port_count} up` : n.uptime ? `up ${n.uptime}` : ""].filter(Boolean).join(" · ");
    g.appendChild(svg("text", { class: "sub", x: 26, y: compact ? 34 : 40 }, sub));
    if (n.issue_count) g.appendChild(svg("circle", { cx: nodeW - 10, cy: 10, r: 6, fill: "#ef4444" }));
    root.appendChild(g);
  });
  root.appendChild(labelLayer);
  return root;
}

// ----------------------------------------------------------------------------- access points
function renderAccessPointsPage() {
  if (S.route.id) return renderAccessPointDetail(S.route.id);
  const aps = S.state.access_points || [];
  const online = aps.filter((ap) => ap.state === "online").length;
  const warnings = aps.reduce((n, ap) => n + (ap.warnings || []).filter((w) => w.severity !== "info").length, 0);
  const page = h("div");
  page.appendChild(h("div", { class: "page-head" }, h("h2", {}, "Access Points"), h("span", { class: "sub" }, `${online}/${aps.length} online · ${warnings} warning${warnings === 1 ? "" : "s"}`)));
  page.appendChild(h("div", { class: "grid grid-cards" }, aps.map((ap) => accessPointCard(ap, { compact: false }))));
  const rows = aps.map((ap) => {
    const uplink = ap.uplink || {};
    return h("tr", { onclick: () => navigate(`#/access-points/${encodeURIComponent(ap.id)}`) },
      h("td", {}, h("div", {}, ap.name), h("div", { class: "dim small mono" }, ap.ip_address || "")),
      h("td", {}, pill(ap.state, toneForStatus(ap.state))),
      h("td", {}, text(ap.uptime)),
      h("td", {}, uplink.expected ? h("span", {}, `${uplink.switch_name || uplink.switch_id} port ${text(uplink.port)}`, uplink.port_label ? h("span", { class: "dim small" }, ` (${uplink.port_label})`) : null) : "—"),
      h("td", {}, pill(uplink.confirmation || "unknown", uplink.confirmation === "confirmed" ? "ok" : uplink.confirmation === "unconfirmed" ? "warn" : "neutral")),
      h("td", {}, `${fmtBps(ap.traffic?.in_bps)} / ${fmtBps(ap.traffic?.out_bps)}`),
      h("td", {}, (ap.warnings || []).slice(0, 2).map((w) => h("div", { class: "small", style: { color: w.severity === "info" ? "var(--info)" : "var(--warn)" } }, w.title))),
    );
  });
  page.appendChild(section("AP status", h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", {}, "AP"), h("th", {}, "State"), h("th", {}, "Uptime"), h("th", {}, "Expected trunk"), h("th", {}, "Evidence"), h("th", {}, "Traffic in/out"), h("th", {}, "Warnings"))), h("tbody", {}, rows))), aps.length));
  return page;
}
function renderAccessPointDetail(id) {
  const ap = apById(id);
  const page = h("div");
  if (!ap) { page.appendChild(h("div", { class: "empty" }, "Unknown access point")); return page; }
  const uplink = ap.uplink || {};
  const ethernet = ap.ethernet || {};
  page.appendChild(h("div", { class: "page-head" }, h("a", { href: "#/access-points", class: "btn btn-sm btn-ghost" }, "← Access Points"), h("h2", {}, ap.name), h("span", { class: "sub" }, [ap.location, ap.model, ap.ip_address].filter(Boolean).join(" · ")), ap.web_url ? h("a", { class: "btn btn-sm btn-ghost", href: ap.web_url, target: "_blank", rel: "noopener" }, "Web UI") : null));
  page.appendChild(accessPointCard(ap, { compact: false }));
  page.appendChild(h("div", { class: "grid grid-2 section" },
    h("div", { class: "panel" }, h("div", { class: "panel-title" }, "SNMP"), h("dl", { class: "kv" }, [
      ["State", h("span", {}, pill(ap.state, toneForStatus(ap.state)), " ", pill(`SNMP ${ap.snmp_status || "—"}`, toneForStatus(ap.snmp_status), ap.last_error || ""))],
      ["Last successful poll", ap.last_successful_poll ? `${fmtDateTime(ap.last_successful_poll)} (${fmtAgo(ap.last_successful_poll)})` : "—"],
      ["Data age", ap.data_age_seconds !== null && ap.data_age_seconds !== undefined ? `${ap.data_age_seconds}s` : "—"],
      ["Last error", text(ap.last_error)],
      ["sysName", text(ap.sys_name)],
      ["sysDescr", text(ap.sys_descr)],
      ["sysLocation", text(ap.sys_location)],
      ["sysContact", text(ap.sys_contact)],
      ["Uptime", text(ap.uptime)],
    ].map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))),
    h("div", { class: "panel" }, h("div", { class: "panel-title" }, "Management & Uplink"), h("dl", { class: "kv" }, [
      ["Management VLAN", vlanTag(ap.management_vlan || 99)],
      ["Management IP", h("span", { class: "mono" }, text(ap.ip_address))],
      ["Expected AP trunk", uplink.expected ? `${uplink.switch_name || uplink.switch_id} port ${text(uplink.port)}${uplink.port_label ? ` (${uplink.port_label})` : ""}` : "—"],
      ["Evidence", pill(uplink.confirmation || "unknown", uplink.confirmation === "confirmed" ? "ok" : uplink.confirmation === "unconfirmed" ? "warn" : "neutral")],
      ["Switch port", `${text(uplink.port_status)}${uplink.speed_mbps ? ` · ${fmtSpeed(uplink.speed_mbps)}` : ""}`],
      ["Trunk VLANs", h("span", {}, uplink.trunk?.native_vlan ? vlanTag(uplink.trunk.native_vlan, false) : null, " ", (uplink.trunk?.tagged_vlans || []).map((v) => vlanTag(v, true)))],
      ["Ethernet interface", ethernet.name ? `${ethernet.name}${ethernet.descr && ethernet.descr !== ethernet.name ? ` (${ethernet.descr})` : ""}` : "—"],
      ["Ethernet link", `${text(ethernet.oper_status || uplink.port_status)}${ethernet.speed_mbps || uplink.speed_mbps ? ` · ${fmtSpeed(ethernet.speed_mbps || uplink.speed_mbps)}` : ""}`],
      ["Traffic", `in ${fmtBps(ap.traffic?.in_bps)} · out ${fmtBps(ap.traffic?.out_bps)}`],
      ["Errors / discards", `${text(ap.errors?.errors)} / ${text(ap.errors?.discards)}${ap.errors?.errors_delta || ap.errors?.discards_delta ? ` (+${ap.errors?.errors_delta || 0} / +${ap.errors?.discards_delta || 0} since last poll)` : ""}`],
      ["Client count", ap.client_count_available ? String(ap.client_count) : ap.client_count_note || "not available by SNMP"],
    ].map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))),
  ));
  page.appendChild(section("Warnings", warningList(ap.warnings || []), (ap.warnings || []).length));
  const interfaces = ap.interfaces || [];
  page.appendChild(section("Interfaces", interfaces.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Link"), h("th", {}, "Speed"), h("th", {}, "Traffic in/out"), h("th", {}, "Errors / discards"))), h("tbody", {}, interfaces.map((i) => h("tr", {}, h("td", {}, h("div", {}, i.name || `if${i.if_index}`), h("div", { class: "dim small" }, i.alias || i.descr || "")), h("td", {}, `${text(i.oper_status)} / admin ${text(i.admin_status)}`), h("td", {}, fmtSpeed(i.speed_mbps)), h("td", {}, `${fmtBps(i.in_bps)} / ${fmtBps(i.out_bps)}`), h("td", {}, `${text(i.errors)} / ${text(i.discards)}`)))))) : h("div", { class: "empty" }, "No interface table available from SNMP."), interfaces.length));
  const problems = (S.state.problems || []).filter((p) => p.affected?.id === ap.id);
  page.appendChild(section("Problems on this AP", problemList(problems, { compact: false }), problems.length));
  return page;
}
function warningList(warnings) {
  if (!warnings.length) return h("div", { class: "empty" }, "No AP warnings");
  return h("div", { class: "list" }, warnings.map((w) => h("div", { class: "list-item" }, h("span", { class: "sev", dataset: { severity: w.severity === "info" ? "info" : "warning" } }), h("div", { class: "body" }, h("div", { class: "title" }, w.title), h("div", { class: "sub" }, w.message)))));
}

// ----------------------------------------------------------------------------- switches
function renderSwitchesPage() {
  if (S.route.id) return renderSwitchDetail(S.route.id);
  const page = h("div");
  page.appendChild(h("div", { class: "page-head" }, h("h2", {}, "Switches"), h("span", { class: "sub" }, "Port grids follow the physical front panel; colours are the expected port role")));
  page.appendChild(h("div", { class: "grid", style: { gridTemplateColumns: "1fr" } }, (S.state.switches || []).map((sw) => switchCard(sw, { compact: true }))));
  page.appendChild(portLegend());
  return page;
}
function switchHeader(sw) {
  return h("div", { class: "head" },
    statusDot(sw.status), h("a", { class: "name", href: `#/switches/${encodeURIComponent(sw.id)}` }, sw.name),
    h("span", { class: "dim small" }, [sw.model, sw.location].filter(Boolean).join(" · ")),
    h("span", { class: "mono dim small" }, sw.ip_address || ""),
    pill(`SNMP ${sw.snmp_status}`, toneForStatus(sw.snmp_status), sw.snmp_error || ""),
    sw.uptime ? pill(`up ${sw.uptime}`, "neutral") : null,
    pill(`${sw.active_port_count}/${sw.port_count} up`, "neutral"),
    sw.issue_count ? pill(`${sw.issue_count} port issue${sw.issue_count === 1 ? "" : "s"}`, "warn") : pill("ports OK", "ok"),
    sw.dante?.relevant ? pill(`Dante QoS ${sw.dante.status}`, sw.dante.status === "ok" ? "ok" : sw.dante.status === "warning" ? "warn" : "neutral", sw.dante.message) : null,
    sw.optional ? pill("optional", "neutral") : null,
    h("span", { class: "grow" }),
    sw.web_url ? h("a", { class: "btn btn-sm btn-ghost", href: sw.web_url, target: "_blank", rel: "noopener" }, "Web UI") : null,
  );
}
function switchCard(sw, { compact }) {
  const card = h("div", { class: "switch-card" });
  card.appendChild(switchHeader(sw));
  card.appendChild(faceplate(sw, { compact }));
  return card;
}
function faceplate(sw, { compact }) {
  const rows = Math.max(1, sw.layout?.rows || 2);
  const sfp = new Set(sw.sfp_ports || []);
  const copper = sw.ports.filter((p) => !sfp.has(p.number));
  const fiber = sw.ports.filter((p) => sfp.has(p.number));
  const selected = S.ui.selectedPort[sw.id];
  const buildCols = (ports) => {
    const cols = [];
    for (let i = 0; i < ports.length; i += rows) cols.push(ports.slice(i, i + rows));
    return cols.map((col) => h("div", { class: "port-col" }, col.map((port) => portButton(sw, port, selected === port.number))));
  };
  const inner = h("div", { class: "faceplate-inner", style: { gridAutoFlow: "column" } }, buildCols(copper), fiber.length ? h("div", { class: "port-gap" }) : null, buildCols(fiber));
  return h("div", { class: "faceplate" }, inner);
}
function portButton(sw, port, selected) {
  const c = colorFor(port.color || "unused");
  const tip = [`Port ${port.number}${port.label ? ` · ${port.label}` : ""}`, port.name ? `${port.name}${port.alias ? ` (${port.alias})` : ""}` : null, `${port.profile} · ${port.oper_status}${port.speed_mbps ? ` ${fmtSpeed(port.speed_mbps)}` : ""}`, port.actual ? `untagged ${port.actual.untagged.join(",") || "-"} tagged ${port.actual.tagged.join(",") || "-"} PVID ${text(port.actual.pvid)}` : "VLANs from config", port.devices?.length ? `devices: ${port.devices.map((d) => d.display_name).join(", ")}` : null, port.lldp?.system_name ? `LLDP: ${port.lldp.system_name}` : null, ...(port.issues || []).map((i) => `! ${i.message}`)].filter(Boolean).join("\n");
  return h("button", { type: "button", class: `port${port.sfp ? " sfp" : ""}${selected ? " selected" : ""}`, title: tip, style: { "--port-color": c.color, "--port-text": c.text || "#fff" }, dataset: { oper: port.oper_status, type: port.type, health: port.health }, onclick: () => { S.ui.selectedPort[sw.id] = selected ? null : port.number; saveUi(); if (S.route.page === "switches" && S.route.id === sw.id) render(); else navigate(`#/switches/${encodeURIComponent(sw.id)}`); } }, h("span", { class: "num" }, String(port.number)), h("span", { class: "led" }));
}
function portLegend() {
  const items = [["control", "CONTROL 10"], ["audio", "AUDIO 20"], ["light", "LIGHT 30"], ["video", "VIDEO 40"], ["mgmt", "MGMT 99"], ["trunk", "TRUNK"], ["ap", "AP trunk"], ["wan", "WAN pass"], ["unused", "unused"]];
  return h("div", { class: "port-legend" }, items.map(([name, label]) => { const c = colorFor(name); return h("span", {}, h("span", { class: "sw", style: { background: c.color, borderColor: c.accent || "rgba(255,255,255,0.2)" } }), label); }), h("span", {}, h("span", { class: "sw", style: { background: "#4ade80" } }), "link up"), h("span", {}, "! = issue"));
}
function renderSwitchDetail(id) {
  const sw = switchById(id);
  const page = h("div");
  if (!sw) { page.appendChild(h("div", { class: "empty" }, "Unknown switch")); return page; }
  page.appendChild(h("div", { class: "page-head" }, h("a", { href: "#/switches", class: "btn btn-sm btn-ghost" }, "← Switches"), h("h2", {}, sw.name), h("span", { class: "sub" }, sw.sys_name ? `${sw.sys_name} · ${sw.sys_descr}` : "")));
  page.appendChild(switchCard(sw, { compact: false }));
  page.appendChild(portLegend());
  const selected = S.ui.selectedPort[sw.id];
  const port = sw.ports.find((p) => p.number === selected);
  page.appendChild(h("div", { class: "port-detail" }, port ? portDetail(sw, port) : h("div", { class: "empty" }, "Select a port to see its expected and actual VLANs, counters, LLDP neighbour and connected devices.")));

  const infoRows = [
    ["Status", `${sw.status}${sw.latency_ms !== null && sw.latency_ms !== undefined ? ` · ${sw.latency_ms} ms` : ""}`],
    ["SNMP", `${sw.snmp_status}${sw.snmp_error ? ` – ${sw.snmp_error}` : ""} · last poll ${fmtAgo(sw.last_poll)}${sw.poll_duration_ms ? ` (${sw.poll_duration_ms} ms)` : ""}`],
    ["VLAN tables", sw.static_polled_at ? `refreshed ${fmtAgo(sw.static_polled_at)}` : "not read yet"],
    ["Uptime", sw.uptime || "—"],
    ["Errors / discards", `${text(sw.error_count)} / ${text(sw.discard_count)} (interface counters)`],
    ["RSTP", sw.stp?.supported ? `${sw.stp.is_root ? "this switch is root" : `root ${sw.stp.root_mac || "?"} via port ${text(sw.stp.root_port)}`} · priority ${text(sw.stp.priority)}` : "not readable via SNMP"],
    ["Dante QoS", sw.dante?.relevant ? `${sw.dante.status}: ${sw.dante.message}` : "n/a"],
  ];
  page.appendChild(h("div", { class: "grid grid-2 section" },
    h("div", { class: "panel" }, h("div", { class: "panel-title" }, "Switch"), h("dl", { class: "kv" }, infoRows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))),
    h("div", { class: "panel" }, h("div", { class: "panel-title" }, "VLANs on this switch"), sw.vlans?.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", {}, "VLAN"), h("th", {}, "Name"), h("th", {}, "Untagged"), h("th", {}, "Tagged"))), h("tbody", {}, sw.vlans.map((v) => h("tr", {}, h("td", {}, vlanTag(v.id)), h("td", {}, v.name), h("td", { class: "mono small" }, compressPorts(v.untagged_ports)), h("td", { class: "mono small" }, compressPorts(v.tagged_ports))))))) : h("div", { class: "empty" }, "VLAN membership not available (SNMP)")),
  ));
  const problems = (S.state.problems || []).filter((p) => p.affected?.id === sw.id || p.affected?.switch_id === sw.id);
  page.appendChild(section("Problems on this switch", problemList(problems, { compact: false }), problems.length));
  return page;
}
function compressPorts(ports) {
  if (!ports || !ports.length) return "—";
  const sorted = [...ports].sort((a, b) => a - b);
  const out = [];
  let start = sorted[0]; let prev = sorted[0];
  for (let i = 1; i <= sorted.length; i++) {
    const cur = sorted[i];
    if (cur === prev + 1) { prev = cur; continue; }
    out.push(start === prev ? String(start) : `${start}-${prev}`);
    start = cur; prev = cur;
  }
  return out.join(", ");
}
function portDetail(sw, port) {
  const exp = port.expected || {};
  const act = port.actual;
  const rows = [
    ["Port", `${port.number}${port.label ? ` · ${port.label}` : ""}${port.name ? ` · ${port.name}` : ""}${port.alias ? ` (${port.alias})` : ""}`],
    ["Profile", `${port.profile} (${port.type}${port.configured ? "" : ", not configured"})${port.notes ? ` – ${port.notes}` : ""}`],
    ["Link", `${port.oper_status} / admin ${port.admin_status}${port.speed_mbps ? ` · ${fmtSpeed(port.speed_mbps)}` : ""}`],
    ["Traffic", `in ${fmtBps(port.in_bps)} · out ${fmtBps(port.out_bps)}`],
    ["Errors / discards", `${text(port.errors)} / ${text(port.discards)}${port.errors_delta || port.discards_delta ? ` (+${port.errors_delta} / +${port.discards_delta} since last poll)` : ""}`],
    ["Expected VLANs", h("span", {}, (exp.untagged || []).map((v) => vlanTag(v, false)), " ", (exp.tagged || []).map((v) => vlanTag(v, true)), exp.pvid !== null && exp.pvid !== undefined ? h("span", { class: "dim small" }, ` PVID ${exp.pvid}`) : null)],
    ["Actual VLANs", act ? h("span", {}, (act.untagged || []).map((v) => vlanTag(v, false)), " ", (act.tagged || []).map((v) => vlanTag(v, true)), h("span", { class: "dim small" }, ` PVID ${text(act.pvid)}`)) : h("span", { class: "dim" }, "not readable")],
    ["Expected neighbour", port.expected_neighbor ? (infraById(port.expected_neighbor)?.name || port.expected_neighbor) : "—"],
    ["LLDP neighbour", port.lldp ? `${port.lldp.system_name || port.lldp.chassis_id} · port ${port.lldp.port_id}${port.lldp.port_description ? ` (${port.lldp.port_description})` : ""}` : "—"],
    ["Infrastructure seen", port.infrastructure?.length ? port.infrastructure.map((i) => i.name).join(", ") : "—"],
  ];
  const devices = port.devices || [];
  return h("div", { class: "panel" },
    h("div", { class: "panel-title" }, `Port ${port.number}`, h("span", { class: "spacer" }), pill(port.health, port.health === "ok" ? "ok" : port.health === "critical" ? "crit" : port.health === "warning" ? "warn" : "info")),
    h("div", { class: "grid grid-2" },
      h("dl", { class: "kv" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      h("div", {},
        h("h4", { class: "small dim", style: { marginBottom: "6px" } }, `DEVICES ON THIS PORT (${devices.length})`),
        devices.length ? h("div", { class: "list" }, devices.map((d) => h("div", { class: "list-item", style: { cursor: "pointer" }, onclick: () => openDrawer({ type: "device", key: d.key }) }, h("div", { class: "body" }, h("div", { class: "title" }, d.display_name), h("div", { class: "sub mono" }, `${text(d.ip_address)} · ${fmtMac(d.mac_address)}`)), statePill(d.state)))) : h("div", { class: "dim small" }, "No devices proven on this port"),
        port.issues?.length ? h("div", { style: { marginTop: "10px" } }, h("h4", { class: "small dim", style: { marginBottom: "6px" } }, "ISSUES"), h("div", { class: "list" }, port.issues.map((i) => h("div", { class: "list-item" }, h("span", { class: "sev", dataset: { severity: i.severity } }), h("div", { class: "body" }, h("div", { class: "sub" }, i.message)))))) : null,
      ),
    ),
  );
}

// ----------------------------------------------------------------------------- devices
const STATE_FILTERS = [["active_located", "Located"], ["active_unlocated", "Unlocated"], ["relocating", "Relocating"], ["stale", "Stale"], ["offline", "Offline"], ["mac_only", "MAC only"]];
function filteredDevices() {
  const f = S.ui.deviceFilters;
  let devices = [...(S.state.devices || [])];
  if (f.states.includes("mac_only")) devices = devices.concat(S.state.device_observations?.mac_only || []);
  const q = (f.q || "").trim().toLowerCase();
  return devices.filter((d) => {
    if (!f.showInfra && d.is_infrastructure) return false;
    if (!f.showIgnored && d.ignored) return false;
    if (f.favorites && !d.favorite) return false;
    if (f.states.length && !f.states.includes(d.state)) return false;
    if (f.vlan && String(d.vlan_id) !== String(f.vlan)) return false;
    if (f.switch && d.location?.switch_id !== f.switch) return false;
    if (f.port && String(d.location?.port) !== String(f.port)) return false;
    if (q) {
      const hay = [d.display_name, d.hostname, d.ip_address, d.mac_address, d.vendor, d.category, d.metadata?.owner, d.metadata?.notes, d.location?.switch_name, d.location?.port_label].filter(Boolean).join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}
function renderDevicesPage() {
  const page = h("div");
  const f = S.ui.deviceFilters;
  const counts = S.state.device_counts || {};
  page.appendChild(h("div", { class: "page-head" }, h("h2", {}, "Devices"), h("span", { class: "sub" }, `${counts.total || 0} known · ${counts.active_located || 0} located · ${counts.active_unlocated || 0} unlocated · ${counts.mac_only || 0} MAC-only hidden`)));
  const update = (patch) => { S.ui.deviceFilters = { ...S.ui.deviceFilters, ...patch }; saveUi(); render(); };
  const stateBtn = (key, label) => h("button", { type: "button", class: `chip-btn${f.states.includes(key) ? " active" : ""}`, onclick: () => update({ states: f.states.includes(key) ? f.states.filter((s) => s !== key) : [...f.states, key] }) }, label, h("span", { class: "n" }, String(counts[key] ?? 0)));
  const filters = h("div", { class: "filters" },
    h("input", { type: "search", placeholder: "Search name, IP, MAC, vendor, owner…", value: f.q, oninput: (e) => { S.ui.deviceFilters.q = e.target.value; saveUi(); renderDeviceTable(); } }),
    STATE_FILTERS.map(([k, l]) => stateBtn(k, l)),
    h("button", { type: "button", class: `chip-btn${f.states.length === 0 ? " active" : ""}`, onclick: () => update({ states: [] }) }, "All states"),
    h("select", { onchange: (e) => update({ vlan: e.target.value }) }, h("option", { value: "" }, "All VLANs"), (S.state.vlans || []).map((v) => h("option", { value: String(v.id), selected: String(v.id) === String(f.vlan) }, `${v.name} (${v.id})`))),
    h("select", { onchange: (e) => update({ switch: e.target.value }) }, h("option", { value: "" }, "All switches"), (S.state.switches || []).map((s) => h("option", { value: s.id, selected: s.id === f.switch }, s.name))),
    h("input", { type: "search", placeholder: "Port", style: { width: "70px" }, value: f.port, oninput: (e) => { S.ui.deviceFilters.port = e.target.value; saveUi(); renderDeviceTable(); } }),
    h("button", { type: "button", class: `chip-btn${f.favorites ? " active" : ""}`, onclick: () => update({ favorites: !f.favorites }) }, "★ Favourites"),
    h("button", { type: "button", class: `chip-btn${f.showInfra ? " active" : ""}`, onclick: () => update({ showInfra: !f.showInfra }) }, "Infrastructure"),
    h("button", { type: "button", class: `chip-btn${f.showIgnored ? " active" : ""}`, onclick: () => update({ showIgnored: !f.showIgnored }) }, "Ignored", h("span", { class: "n" }, String(counts.ignored || 0))),
    h("button", { type: "button", class: "chip-btn", onclick: () => update({ q: "", states: ["active_located"], vlan: "", switch: "", port: "", showInfra: false, showIgnored: false, favorites: false }) }, "Reset"),
  );
  page.appendChild(filters);
  page.appendChild(h("div", { id: "deviceTable" }));
  setTimeout(renderDeviceTable, 0);
  return page;
}
function renderDeviceTable() {
  const host = document.getElementById("deviceTable");
  if (!host) return;
  clear(host);
  const devices = filteredDevices();
  if (!devices.length) { host.appendChild(h("div", { class: "empty" }, "No devices match the current filters.")); return; }
  const rows = devices.map((d) => {
    const loc = d.location;
    const locText = loc ? `${loc.switch_name} · ${loc.port}${loc.port_label ? ` ${loc.port_label}` : ""}${loc.confidence === "last_known" ? " (last known)" : loc.confidence === "uplink" ? " (uplink)" : ""}` : d.path ? `behind ${d.path.switch_name} · ${d.path.port}` : "—";
    return h("tr", { class: S.drawer?.key === d.key ? "selected" : "", onclick: () => openDrawer({ type: "device", key: d.key }) },
      h("td", {}, statePill(d.state)),
      h("td", {}, h("div", {}, d.favorite ? h("span", { class: "star" }, "★ ") : null, d.display_name, d.ignored ? h("span", { class: "dim small" }, " (ignored)") : null), h("div", { class: "dim small" }, [d.vendor, d.category, d.metadata?.owner].filter(Boolean).join(" · "))),
      h("td", { class: "mono" }, text(d.ip_address)),
      h("td", { class: "mono hide-mobile" }, fmtMac(d.mac_address)),
      h("td", {}, d.vlan_id ? vlanTag(d.vlan_id) : "—"),
      h("td", {}, locText),
      h("td", { class: "hide-mobile dim small" }, fmtAgo(d.last_seen)),
    );
  });
  host.appendChild(h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", {}, "State"), h("th", {}, "Device"), h("th", {}, "IP"), h("th", { class: "hide-mobile" }, "MAC"), h("th", {}, "VLAN"), h("th", {}, "Switch / port"), h("th", { class: "hide-mobile" }, "Last seen"))), h("tbody", {}, rows))));
}

// ----------------------------------------------------------------------------- drawer (device / infra details)
function openDrawer(spec) { S.drawer = spec; refreshDrawer(); }
function closeDrawer() { S.drawer = null; document.getElementById("drawer").hidden = true; document.getElementById("drawerBackdrop").hidden = true; document.querySelectorAll("tr.selected").forEach((r) => r.classList.remove("selected")); }
function refreshDrawer() {
  const drawer = document.getElementById("drawer");
  const backdrop = document.getElementById("drawerBackdrop");
  if (!S.drawer) { drawer.hidden = true; backdrop.hidden = true; return; }
  const scroll = drawer.scrollTop;
  const active = document.activeElement;
  const activeId = active && drawer.contains(active) ? active.id : null;
  if (activeId && activeId.startsWith("meta-")) return; // don't clobber an edit in progress
  clear(drawer);
  if (S.drawer.type === "device") {
    const device = deviceByKey(S.drawer.key);
    if (!device) { closeDrawer(); return; }
    drawer.appendChild(deviceDrawer(device));
  } else if (S.drawer.type === "infra") {
    const item = infraById(S.drawer.id);
    if (!item) { closeDrawer(); return; }
    drawer.appendChild(infraDrawer(item));
  }
  drawer.hidden = false;
  backdrop.hidden = false;
  drawer.scrollTop = scroll;
}
function drawerHead(title, subtitle) {
  return h("div", { class: "head" }, h("div", { class: "grow" }, h("h3", {}, title), subtitle ? h("div", { class: "dim small" }, subtitle) : null), h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: closeDrawer }, "✕"));
}
function kvSection(title, rows) {
  return h("div", { class: "section" }, h("h4", {}, title), h("dl", { class: "kv" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])));
}
function deviceDrawer(d) {
  const loc = d.location;
  const m = d.metadata || {};
  const el = h("div");
  el.appendChild(drawerHead(d.display_name, [d.vendor, d.hostname && d.hostname !== d.display_name ? d.hostname : null].filter(Boolean).join(" · ")));
  el.appendChild(h("div", { class: "flex", style: { marginBottom: "12px" } }, statePill(d.state), d.is_infrastructure ? pill("infrastructure", "info") : null, d.favorite ? pill("★ favourite", "warn") : null, d.ignored ? pill("ignored", "neutral") : null, d.web_url ? h("a", { class: "btn btn-sm btn-ghost", href: d.web_url, target: "_blank", rel: "noopener" }, "Open web UI") : null));
  el.appendChild(kvSection("Network", [
    ["IP address", h("span", { class: "mono" }, text(d.ip_address))],
    ["VLAN", d.vlan_id ? h("span", {}, vlanTag(d.vlan_id), ` ${d.vlan_name}`) : "—"],
    ["MAC", h("span", { class: "mono" }, fmtMac(d.mac_address))],
    d.mac_addresses?.length > 1 ? ["Other MACs", h("span", { class: "mono" }, d.mac_addresses.slice(1).map(fmtMac).join(", "))] : null,
    d.ip_addresses?.length > 1 ? ["Other IPs", h("span", { class: "mono" }, d.ip_addresses.filter((ip) => ip !== d.ip_address).join(", "))] : null,
    ["Ping", d.ping_status ? `${d.ping_status}${d.latency_ms !== null && d.latency_ms !== undefined ? ` · ${d.latency_ms} ms` : ""}` : "not pinged"],
  ].filter(Boolean)));
  el.appendChild(kvSection("Location", [
    ["Switch / port", loc ? h("a", { href: `#/switches/${encodeURIComponent(loc.switch_id)}`, onclick: () => { S.ui.selectedPort[loc.switch_id] = loc.port; saveUi(); closeDrawer(); } }, `${loc.switch_name} port ${loc.port}${loc.port_label ? ` (${loc.port_label})` : ""}`) : "not proven"],
    ["Confidence", loc ? { edge: "exact – learned on an access port", edge_unconfigured: "learned on an unconfigured port", uplink: "uplink port of this device", last_known: "last known location (not current)" }[loc.confidence] || loc.confidence : "—"],
    ["Port link", loc ? text(loc.port_status) : "—"],
    d.path ? ["Path hint", d.path.message] : null,
  ].filter(Boolean)));
  el.appendChild(kvSection("Presence", [
    ["State", `${d.state_label}${d.previous_state ? ` (was ${d.previous_state.replace("_", " ")})` : ""}`],
    ["Confirmed by", d.confirmed_by?.length ? d.confirmed_by.join(", ") : "nothing current"],
    ["Sources", (d.sources || []).join(", ")],
    ["First seen", fmtDateTime(d.first_seen)],
    ["Last seen", `${fmtDateTime(d.last_seen)} (${fmtAgo(d.last_seen)})`],
  ]));
  if (m.editable) el.appendChild(metadataForm(d));
  else el.appendChild(h("div", { class: "form-note" }, "This device has no MAC address yet, so it cannot be saved as a known device."));
  if (!d.is_infrastructure && (d.state === "offline" || d.state === "stale")) {
    el.appendChild(h("div", { class: "section" }, h("button", { type: "button", class: "btn btn-sm btn-danger", onclick: async () => { if (!confirm("Forget this device's history? It will reappear if it is seen again.")) return; try { await api(`/api/devices/${encodeURIComponent(d.key)}`, { method: "DELETE" }); toast("Device forgotten", "ok"); closeDrawer(); } catch (err) { toast(err.message, "crit"); } } }, "Forget device")));
  }
  return el;
}
function metadataForm(d) {
  const m = d.metadata || {};
  const field = (label, name, value, opts = {}) => h("label", { class: "field" }, label, opts.textarea ? h("textarea", { id: `meta-${name}`, name, placeholder: opts.placeholder || "" }, value || "") : h("input", { id: `meta-${name}`, name, type: "text", value: value || "", placeholder: opts.placeholder || "", list: opts.list }));
  const form = h("form", { class: "form section", onsubmit: async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const payload = { identity_key: m.identity_key, mac_address: d.mac_address, display_name: fd.get("display_name").trim(), owner: fd.get("owner").trim(), device_type: fd.get("device_type").trim(), criticality: fd.get("criticality").trim(), asset_tag: fd.get("asset_tag").trim(), notes: fd.get("notes").trim(), favorite: fd.get("favorite") === "on", ignored: fd.get("ignored") === "on", mac_addresses: fd.get("mac_addresses").split(/[\s,;]+/).filter(Boolean) };
    const button = e.target.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await api("/api/devices/metadata", { method: "PUT", body: JSON.stringify({ metadata: payload }) });
      toast("Device saved – applying on next poll", "ok");
      document.activeElement?.blur();
    } catch (err) { toast(`Save failed: ${err.message}`, "crit", 7000); }
    finally { button.disabled = false; }
  } },
    h("h4", {}, m.saved ? "Known device" : "Save as known device"),
    h("div", { class: "form-row" }, field("Display name", "display_name", m.display_name, { placeholder: d.display_name }), field("Owner", "owner", m.owner)),
    h("div", { class: "form-row" }, field("Category", "device_type", m.device_type, { placeholder: "e.g. console, Dante, lighting desk", list: "categories" }), field("Criticality", "criticality", m.criticality, { placeholder: "show-critical / normal" }), field("Asset tag", "asset_tag", m.asset_tag)),
    h("datalist", { id: "categories" }, ["console", "Dante", "lighting", "video", "laptop", "media server", "switch", "AP", "printer", "camera", "PLC", "other"].map((c) => h("option", { value: c }))),
    field("Notes", "notes", m.notes, { textarea: true }),
    field("MAC addresses (aliases, one per line)", "mac_addresses", (m.mac_addresses || []).join("\n"), { textarea: true, placeholder: d.mac_address }),
    h("div", { class: "form-row" }, h("label", { class: "field check" }, h("input", { type: "checkbox", name: "favorite", id: "meta-favorite", checked: !!m.favorite }), "Favourite"), h("label", { class: "field check" }, h("input", { type: "checkbox", name: "ignored", id: "meta-ignored", checked: !!m.ignored }), "Ignore (hide from lists and problems)")),
    h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save"), m.saved ? h("button", { type: "button", class: "btn btn-sm btn-danger", onclick: async () => { if (!confirm("Remove the saved name/notes for this device?")) return; try { await api(`/api/devices/metadata/${encodeURIComponent(m.identity_key)}`, { method: "DELETE" }); toast("Metadata removed", "ok"); } catch (err) { toast(err.message, "crit"); } } }, "Remove") : null, m.updated_at ? h("span", { class: "form-note" }, `saved ${fmtAgo(m.updated_at)}`) : null),
    h("div", { class: "form-note" }, "Empty fields are saved as empty and clear the previous value. Aliases let one device keep its name when it switches between Wi-Fi and cable."),
  );
  return form;
}
function infraDrawer(item) {
  const snmp = item.snmp || {};
  const el = h("div");
  el.appendChild(drawerHead(item.name, [item.vendor, item.model, item.location].filter(Boolean).join(" · ")));
  el.appendChild(h("div", { class: "flex", style: { marginBottom: "12px" } }, pill(item.status, toneForStatus(item.status)), item.planned ? pill("planned", "neutral") : null, item.optional ? pill("optional", "neutral") : null, item.web_url ? h("a", { class: "btn btn-sm btn-ghost", href: item.web_url, target: "_blank", rel: "noopener" }, "Open web UI") : null, item.kind === "switch" ? h("a", { class: "btn btn-sm", href: `#/switches/${encodeURIComponent(item.id)}`, onclick: closeDrawer }, "Ports") : null));
  el.appendChild(kvSection("Device", [["Kind", item.kind], ["IP", h("span", { class: "mono" }, text(item.ip_address))], ["VLAN", vlanTag(item.vlan)], ["Ping", item.check ? `${item.check.status}${item.check.latency_ms !== null && item.check.latency_ms !== undefined ? ` · ${item.check.latency_ms} ms` : ""}` : "—"], ["SNMP", `${snmp.status || "—"}${snmp.error ? ` – ${snmp.error}` : ""}`], ["System name", text(snmp.sys_name)], ["Description", text(snmp.sys_descr)], ["Uptime", text(snmp.uptime)], ["Last poll", snmp.last_poll ? `${fmtAgo(snmp.last_poll)}${snmp.duration_ms ? ` (${snmp.duration_ms} ms)` : ""}` : "—"]]));
  const device = (S.state.devices || []).find((d) => d.infra_id === item.id);
  if (device?.location) el.appendChild(kvSection("Connected to", [["Switch / port", h("a", { href: `#/switches/${encodeURIComponent(device.location.switch_id)}`, onclick: closeDrawer }, `${device.location.switch_name} port ${device.location.port}${device.location.port_label ? ` (${device.location.port_label})` : ""}`)], ["MAC", h("span", { class: "mono" }, fmtMac(device.mac_address))]]));
  const problems = (S.state.problems || []).filter((p) => p.affected?.id === item.id);
  el.appendChild(h("div", { class: "section" }, h("h4", {}, `Problems (${problems.length})`), problemList(problems, { compact: true })));
  return el;
}

// ----------------------------------------------------------------------------- problems
function problemList(problems, { compact }) {
  if (!problems.length) return h("div", { class: "empty" }, "No problems 🎉");
  return h("div", {}, problems.map((p) => problemCard(p, compact)));
}
function problemCard(p, compact) {
  const aff = p.affected || {};
  const link = aff.kind === "ap" ? `#/access-points/${encodeURIComponent(aff.id)}` : aff.kind === "switch" || aff.switch_id ? `#/switches/${encodeURIComponent(aff.switch_id || aff.id)}` : null;
  const target = link ? h("a", { href: link, onclick: () => { if (aff.port && (aff.switch_id || aff.id) && aff.kind !== "ap") { S.ui.selectedPort[aff.switch_id || aff.id] = Number(aff.port); saveUi(); } } }, aff.name || aff.id) : aff.kind === "device" ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); openDrawer({ type: "device", key: aff.id }); } }, aff.name || aff.id) : h("span", {}, aff.name || aff.id || "");
  return h("div", { class: `problem${p.acknowledged_at ? " acked" : ""}`, dataset: { severity: p.severity } },
    h("span", { class: "sev", dataset: { severity: p.severity } }),
    h("div", {},
      h("div", { class: "title" }, p.title),
      compact ? null : h("div", { class: "detail" }, p.detail),
      compact || !p.fix ? null : h("div", { class: "fix" }, "Suggested fix: ", p.fix),
      h("div", { class: "meta" }, h("span", {}, pill(p.category.replace(/_/g, " "), "neutral")), h("span", {}, "Affects: ", target, aff.port ? ` port ${aff.port}` : ""), h("span", {}, `since ${fmtDateTime(p.first_seen)}`), h("span", {}, `last seen ${fmtAgo(p.last_seen)}`), p.acknowledged_at ? h("span", {}, `acknowledged ${fmtAgo(p.acknowledged_at)}`) : null),
    ),
    compact ? h("div") : h("div", { class: "actions" }, h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: async () => { try { await api(`/api/problems/${encodeURIComponent(p.id)}/ack`, { method: "POST", body: JSON.stringify({ acknowledged: !p.acknowledged_at }) }); } catch (err) { toast(err.message, "crit"); } } }, p.acknowledged_at ? "Unacknowledge" : "Acknowledge")),
  );
}
function renderProblemsPage() {
  const page = h("div");
  const counts = S.state.problem_counts || {};
  const all = S.state.problems || [];
  const filter = S.ui.problemFilter || "all";
  const visible = all.filter((p) => (filter === "all" || p.severity === filter) && (S.ui.showAcked || !p.acknowledged_at));
  const btn = (key, label, n) => h("button", { type: "button", class: `chip-btn${filter === key ? " active" : ""}`, onclick: () => { S.ui.problemFilter = key; saveUi(); render(); } }, label, h("span", { class: "n" }, String(n)));
  page.appendChild(h("div", { class: "page-head" }, h("h2", {}, "Problems"), h("span", { class: "sub" }, "Production checks against the current network plan (VLAN 10/20/30/40/99, no 50, WAN_PASS 90 only on passthrough ports)")));
  page.appendChild(h("div", { class: "filters" }, btn("all", "All", all.length), btn("critical", "Critical", counts.critical || 0), btn("warning", "Warning", counts.warning || 0), btn("info", "Info", counts.info || 0), h("button", { type: "button", class: `chip-btn${S.ui.showAcked ? " active" : ""}`, onclick: () => { S.ui.showAcked = !S.ui.showAcked; saveUi(); render(); } }, "Show acknowledged", h("span", { class: "n" }, String(counts.acknowledged || 0)))));
  page.appendChild(problemList(visible, { compact: false }));
  return page;
}

// ----------------------------------------------------------------------------- settings
function renderSettings() {
  const page = h("div");
  const settings = S.state?.settings;
  page.appendChild(h("div", { class: "page-head" }, h("h2", {}, "Settings"), h("span", { class: "sub" }, "Stored in config/network.yaml on the Pi. Changes apply on the next poll.")));
  if (!settings || settings.error) { page.appendChild(h("div", { class: "empty" }, settings?.error || "Settings not loaded")); return page; }
  const block = (key, title, body, subtitle) => {
    const d = h("details", { class: "block", open: !!S.ui.settingsOpen[key], ontoggle: (e) => { S.ui.settingsOpen[key] = e.target.open; saveUi(); } }, h("summary", {}, title, subtitle ? h("span", { class: "dim small" }, subtitle) : null), h("div", { class: "body" }, body));
    return d;
  };
  page.appendChild(block("monitoring", "Polling & thresholds", monitoringForm(settings)));
  page.appendChild(block("snmp", "SNMP defaults & test", snmpForm(settings)));
  page.appendChild(block("discovery", "Discovery", discoveryForm(settings)));
  page.appendChild(block("station", "Monitor station & router", stationRouterForm(settings)));
  page.appendChild(block("switches", "Switches & port profiles", switchesForm(settings), `${settings.switches.length} switches`));
  page.appendChild(block("aps", "Access points", apsForm(settings), `${settings.access_points.length} configured`));
  page.appendChild(block("vlans", "VLAN definitions", vlansForm(settings), `${settings.vlans.length} VLANs`));
  page.appendChild(block("policy", "Production policy", policyForm(settings)));
  page.appendChild(block("profiles", "Port profiles", profilesForm(settings)));
  page.appendChild(block("inventory", "Expected device inventory", inventoryForm(settings), `${settings.inventory.length} entries`));
  page.appendChild(block("internet", "Internet probes", internetForm(settings)));
  return page;
}
async function saveSettings(section, payload, form) {
  const button = form.querySelector("button[type=submit]");
  if (button) button.disabled = true;
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ settings: { [section]: payload } }) });
    toast("Settings saved – re-polling", "ok");
  } catch (err) {
    toast(`Save failed: ${err.message}`, "crit", 8000);
  } finally {
    if (button) button.disabled = false;
  }
}
function numField(label, name, value, hint) {
  return h("label", { class: "field" }, label, h("input", { type: "number", name, value: value ?? "", min: 0, step: 1 }), hint ? h("span", { class: "form-note" }, hint) : null);
}
function textField(label, name, value, opts = {}) {
  return h("label", { class: "field" }, label, h("input", { type: opts.type || "text", name, value: value ?? "", placeholder: opts.placeholder || "" }), opts.hint ? h("span", { class: "form-note" }, opts.hint) : null);
}
function checkField(label, name, checked) {
  return h("label", { class: "field check" }, h("input", { type: "checkbox", name, checked: !!checked }), label);
}
function selectField(label, name, value, options) {
  return h("label", { class: "field" }, label, h("select", { name }, options.map(([v, l]) => h("option", { value: v, selected: String(v) === String(value ?? "") }, l))));
}
function formData(form) { const fd = new FormData(form); const out = {}; for (const [k, v] of fd.entries()) out[k] = v; form.querySelectorAll("input[type=checkbox]").forEach((c) => { out[c.name] = c.checked; }); return out; }

function monitoringForm(s) {
  const m = s.monitoring;
  const form = h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); const d = formData(e.target); saveSettings("monitoring", d, e.target); } },
    h("div", { class: "form-row" },
      numField("Fast poll interval (s)", "fast_poll_interval_seconds", m.fast_poll_interval_seconds, "ping + SNMP + MAC tables"),
      numField("Full discovery interval (s)", "full_scan_interval_seconds", m.full_scan_interval_seconds, "fping/nmap sweep of all VLAN subnets"),
      numField("Min. time between full scans (s)", "full_scan_min_interval_seconds", m.full_scan_min_interval_seconds, "manual full scans are rate limited"),
      numField("Refresh cooldown (s)", "refresh_cooldown_seconds", m.refresh_cooldown_seconds, "shared by all browsers"),
      numField("Static tables every N cycles", "static_tables_every_cycles", m.static_tables_every_cycles, "VLAN membership, STP, QoS"),
    ),
    h("div", { class: "form-row" },
      numField("Stale after (s)", "stale_after_seconds", m.stale_after_seconds),
      numField("Relocating window (s)", "relocating_seconds", m.relocating_seconds),
      numField("Offline after (s)", "offline_after_seconds", m.offline_after_seconds),
      numField("Targeted refresh cooldown (s)", "targeted_refresh_cooldown_seconds", m.targeted_refresh_cooldown_seconds, "after a port changes state"),
      numField("Port error threshold / poll", "port_error_threshold", m.port_error_threshold),
    ),
    h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save")),
  );
  return form;
}
function snmpForm(s) {
  const snmp = s.snmp || {};
  const form = h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); const d = formData(e.target); saveSettings("snmp", { enabled: d.enabled, version: d.version, timeout_seconds: d.timeout_seconds, retries: d.retries, max_parallel_hosts: d.max_parallel_hosts, community: d.community }, e.target); } },
    h("div", { class: "form-row" }, checkField("SNMP enabled", "enabled", snmp.enabled !== false), selectField("Version", "version", snmp.version || "2c", [["2c", "2c"], ["1", "1"]]), numField("Timeout (s)", "timeout_seconds", snmp.timeout_seconds ?? 2), numField("Retries", "retries", snmp.retries ?? 1), numField("Parallel hosts", "max_parallel_hosts", snmp.max_parallel_hosts ?? 3), textField("Fallback community", "community", snmp.community || "", { hint: "used when a device has none; ABOUTUS_SNMP_COMMUNITY in .env overrides" })),
    h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save")),
  );
  const test = h("form", { class: "form", style: { marginTop: "14px" }, onsubmit: async (e) => { e.preventDefault(); const d = formData(e.target); const out = e.target.querySelector(".result"); out.textContent = "Testing…"; try { const r = await api("/api/snmp/test", { method: "POST", body: JSON.stringify({ ip_address: d.ip_address, community: d.community, version: d.version }) }); out.textContent = r.result.status === "ok" ? `OK (${r.result.latency_ms} ms): ${r.result.sys_name} – ${r.result.sys_descr} · up ${r.result.uptime} · profile ${r.result.vendor_profile}` : `Failed: ${r.result.error}`; } catch (err) { out.textContent = `Failed: ${err.message}`; } } },
    h("h4", { class: "small dim" }, "TEST SNMP ACCESS"),
    h("div", { class: "form-row" }, textField("IP address", "ip_address", "", { placeholder: "192.168.99.10" }), textField("Community", "community", ""), selectField("Version", "version", "2c", [["2c", "2c"], ["1", "1"]])),
    h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-sm" }, "Test"), h("span", { class: "result form-note" })),
  );
  return h("div", {}, form, test);
}
function discoveryForm(s) {
  const d0 = s.discovery || {};
  return h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); saveSettings("discovery", formData(e.target), e.target); } },
    h("div", { class: "form-row" }, checkField("Discovery enabled", "enabled", d0.enabled !== false), selectField("Tool", "tool", d0.tool || "fping", [["fping", "fping (fast, recommended)"], ["nmap", "nmap -sn"]]), numField("Timeout per subnet (s)", "timeout_seconds_per_subnet", d0.timeout_seconds_per_subnet ?? 20), numField("Parallel subnets", "max_parallel_scans", d0.max_parallel_scans ?? 2)),
    h("div", { class: "form-note" }, "Only VLANs with a subnet and monitor = true are swept. VLAN 90 WAN_PASS is never scanned."),
    h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save")),
  );
}
function stationRouterForm(s) {
  const st = s.station || {};
  const r = s.router || {};
  return h("div", { class: "grid grid-2" },
    h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); saveSettings("station", formData(e.target), e.target); } },
      h("h4", { class: "small dim" }, "MONITOR STATION (RASPBERRY PI)"),
      h("div", { class: "form-row" }, textField("Name", "name", st.name), textField("Hostname", "hostname", st.hostname), textField("IP address", "ip_address", st.ip_address), numField("VLAN", "vlan", st.vlan ?? 99), textField("Model", "model", st.model), textField("Web URL", "web_url", st.web_url)),
      h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save")),
    ),
    h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); const d = formData(e.target); saveSettings("router", { ...d, snmp: { version: d.snmp_version, community: d.snmp_community, community_env: d.snmp_community_env } }, e.target); } },
      h("h4", { class: "small dim" }, "ROUTER"),
      h("div", { class: "form-row" }, textField("Name", "name", r.name), textField("Vendor", "vendor", r.vendor), textField("Model", "model", r.model), textField("IP address", "ip_address", r.ip_address), textField("Web URL", "web_url", r.web_url), textField("Location", "location", r.location), textField("Uplink port name", "uplink_port", r.uplink_port, { placeholder: "LAN-1" })),
      h("div", { class: "form-row" }, checkField("SNMP enabled", "snmp_enabled", r.snmp_enabled !== false), selectField("SNMP version", "snmp_version", r.snmp?.version || "2c", [["2c", "2c"], ["1", "1"]]), textField("SNMP community env", "snmp_community_env", r.snmp?.community_env || ""), textField("SNMP community", "snmp_community", r.snmp?.community || "")),
      h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save")),
    ),
  );
}
function switchesForm(s) {
  const profiles = Object.keys(s.port_profiles || {});
  const infraOptions = [["", "—"], [s.router?.id || "lancom-router", s.router?.name || "Router"], ...s.switches.map((sw) => [sw.id, sw.name]), ...s.access_points.map((ap) => [ap.id, ap.name])];
  const container = h("div", {});
  const renderSwitch = (sw, index) => {
    const raw = sw.ports || {};
    const expanded = expandPorts(raw, sw.port_count || 28);
    const portRows = [];
    for (let n = 1; n <= (sw.port_count || 28); n++) {
      const p = expanded[n] || {};
      portRows.push(h("tr", {}, h("td", { class: "mono" }, String(n)), h("td", {}, h("select", { name: `port_${n}_profile` }, [["UNUSED", "—"], ...profiles.filter((x) => x !== "UNUSED").map((x) => [x, x])].map(([v, l]) => h("option", { value: v, selected: (p.profile || "UNUSED") === v }, l)))), h("td", {}, h("input", { name: `port_${n}_label`, value: p.label || "", placeholder: "label" })), h("td", {}, h("select", { name: `port_${n}_neighbor` }, infraOptions.map(([v, l]) => h("option", { value: v, selected: (p.neighbor || "") === v }, l)))), h("td", {}, h("input", { name: `port_${n}_notes`, value: p.notes || "", placeholder: "notes" }))));
    }
    const form = h("form", { class: "form", onsubmit: (e) => {
      e.preventDefault();
      const d = formData(e.target);
      const ports = {};
      for (let n = 1; n <= (Number(d.port_count) || 28); n++) {
        const profile = d[`port_${n}_profile`];
        if (!profile || profile === "UNUSED") { if (d[`port_${n}_label`] || d[`port_${n}_notes`]) ports[String(n)] = { profile: "UNUSED", label: d[`port_${n}_label`], notes: d[`port_${n}_notes`] }; continue; }
        ports[String(n)] = { profile, label: d[`port_${n}_label`], neighbor: d[`port_${n}_neighbor`], notes: d[`port_${n}_notes`] };
      }
      const list = s.switches.map((other, i) => (i === index ? { ...other, ...switchPayload(d), ports } : other));
      saveSettings("switches", list, e.target);
    } },
      h("div", { class: "form-row" }, textField("ID", "id", sw.id), textField("Name", "name", sw.name), textField("Hostname", "hostname", sw.hostname), textField("Role", "role", sw.role, { placeholder: "core / stage / expansion" }), textField("Vendor", "vendor", sw.vendor), textField("Model", "model", sw.model), textField("Location", "location", sw.location)),
      h("div", { class: "form-row" }, textField("IP address", "ip_address", sw.ip_address), textField("Web URL", "web_url", sw.web_url), numField("Port count", "port_count", sw.port_count ?? 28), numField("Grid rows", "layout_rows", sw.layout?.rows ?? 2), textField("SFP ports", "sfp_ports", (sw.sfp_ports || []).join(","), { placeholder: "25,26,27,28" })),
      h("div", { class: "form-row" }, checkField("Enabled", "enabled", sw.enabled !== false), checkField("Optional (offline = warning)", "optional", sw.optional), checkField("Expected RSTP root", "rstp_root_expected", sw.rstp_root_expected), checkField("Dante capable", "dante_capable", sw.dante_capable), checkField("Dante QoS verified manually", "dante_qos_verified", sw.dante_qos_verified), checkField("EEE disabled verified manually", "eee_disabled_verified", sw.eee_disabled_verified)),
      h("div", { class: "form-row" }, checkField("SNMP enabled", "snmp_enabled", sw.snmp_enabled !== false), selectField("SNMP version", "snmp_version", sw.snmp?.version || "2c", [["2c", "2c"], ["1", "1"]]), textField("SNMP community env", "snmp_community_env", sw.snmp?.community_env || ""), textField("SNMP community", "snmp_community", sw.snmp?.community || "")),
      h("details", {}, h("summary", { class: "small" }, "Expected port profiles"), h("div", { class: "table-wrap", style: { marginTop: "8px" } }, h("table", { class: "ports-editor" }, h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Profile"), h("th", {}, "Label"), h("th", {}, "Expected neighbour"), h("th", {}, "Notes"))), h("tbody", {}, portRows)))),
      h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save switch"), h("button", { type: "button", class: "btn btn-sm btn-danger", onclick: () => { if (!confirm(`Remove ${sw.name} from the monitor configuration?`)) return; saveSettings("switches", s.switches.filter((_x, i) => i !== index), form); } }, "Remove")),
    );
    return h("details", { class: "block" }, h("summary", {}, sw.name, h("span", { class: "dim small" }, `${sw.ip_address || ""} · ${sw.model || ""}`)), h("div", { class: "body" }, form));
  };
  s.switches.forEach((sw, index) => container.appendChild(renderSwitch(sw, index)));
  container.appendChild(h("button", { type: "button", class: "btn btn-sm", onclick: () => { const name = prompt("Name of the new switch"); if (!name) return; saveSettings("switches", [...s.switches, { id: name.toLowerCase().replace(/[^a-z0-9]+/g, "-"), name, port_count: 28, layout: { rows: 2, columns: 14 }, ports: "default", snmp: { version: "2c", community: "" } }], container); } }, "+ Add switch"));
  return container;
}
function switchPayload(d) {
  return { id: d.id, name: d.name, hostname: d.hostname, role: d.role, vendor: d.vendor, model: d.model, location: d.location, ip_address: d.ip_address, web_url: d.web_url, port_count: Number(d.port_count) || 28, layout: { rows: Number(d.layout_rows) || 2 }, sfp_ports: String(d.sfp_ports || "").split(/[,\s]+/).filter(Boolean).map(Number), enabled: d.enabled, optional: d.optional, rstp_root_expected: d.rstp_root_expected, dante_capable: d.dante_capable, dante_qos_verified: d.dante_qos_verified, eee_disabled_verified: d.eee_disabled_verified, snmp_enabled: d.snmp_enabled, snmp: { version: d.snmp_version, community: d.snmp_community, community_env: d.snmp_community_env } };
}
function expandPorts(raw, count) {
  const out = {};
  const assign = (n, spec) => { if (n < 1 || n > count) return; out[n] = typeof spec === "string" ? { profile: spec } : { ...spec }; };
  if (raw === "default") { [["1-8", "CONTROL"], ["9-12", "AUDIO"], ["13-16", "LIGHT"], ["17-20", "VIDEO"]].forEach(([k, v]) => expandKey(k).forEach((n) => assign(n, v))); return out; }
  Object.entries(raw || {}).forEach(([key, spec]) => { if (key.startsWith("_")) return; expandKey(key).forEach((n) => assign(n, spec)); });
  return out;
}
function expandKey(key) {
  const out = [];
  String(key).split(/[,\s]+/).forEach((part) => { const m = part.match(/^(\d+)\s*-\s*(\d+)$/); if (m) { for (let i = Number(m[1]); i <= Number(m[2]); i++) out.push(i); } else if (/^\d+$/.test(part)) out.push(Number(part)); });
  return out;
}
function apsForm(s) {
  const container = h("div", {});
  const list = s.access_points || [];
  const switchOptions = [["", "—"], ...(s.switches || []).map((sw) => [sw.id, sw.name])];
  list.forEach((ap, index) => {
    container.appendChild(h("form", { class: "form", style: { marginBottom: "12px" }, onsubmit: (e) => { e.preventDefault(); const d = formData(e.target); const updated = list.map((o, i) => (i === index ? { ...o, ...d, snmp: { version: d.snmp_version, community: d.snmp_community, community_env: d.snmp_community_env } } : o)); saveSettings("access_points", updated, e.target); } },
      h("div", { class: "form-row" }, textField("ID", "id", ap.id), textField("Name", "name", ap.name), textField("Vendor", "vendor", ap.vendor || "tp-link"), textField("Model", "model", ap.model), textField("IP address", "ip_address", ap.ip_address), textField("Location", "location", ap.location), textField("Web URL", "web_url", ap.web_url)),
      h("div", { class: "form-row" }, numField("Management VLAN", "management_vlan", ap.management_vlan ?? ap.vlan ?? 99), selectField("Expected switch", "expected_switch_id", ap.expected_switch_id || "", switchOptions), numField("Expected port", "expected_switch_port", ap.expected_switch_port || ""), textField("Expected sysName", "expected_sys_name", ap.expected_sys_name || ap.name), textField("Expected sysLocation", "expected_sys_location", ap.expected_sys_location || ap.location)),
      h("div", { class: "form-row" }, checkField("Enabled", "enabled", ap.enabled !== false), checkField("Planned (not installed yet)", "planned", ap.planned), checkField("SNMP enabled", "snmp_enabled", ap.snmp_enabled), selectField("SNMP version", "snmp_version", ap.snmp?.version || "2c", [["2c", "2c"], ["1", "1"]]), textField("SNMP community env", "snmp_community_env", ap.snmp?.community_env || ""), textField("SNMP community", "snmp_community", ap.snmp?.community || "")),
      h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save"), h("button", { type: "button", class: "btn btn-sm btn-danger", onclick: (e) => { if (confirm(`Remove ${ap.name}?`)) saveSettings("access_points", list.filter((_o, i) => i !== index), e.target.closest("form")); } }, "Remove")),
    ));
  });
  container.appendChild(h("button", { type: "button", class: "btn btn-sm", onclick: () => { const name = prompt("Name of the access point"); if (!name) return; saveSettings("access_points", [...list, { id: name.toLowerCase().replace(/[^a-z0-9]+/g, "-"), name, vendor: "tp-link", model: "EAP650", management_vlan: 99, planned: true, snmp_enabled: false, snmp: { version: "2c", community_env: "" } }], container); } }, "+ Add access point"));
  return container;
}
function vlansForm(s) {
  const rows = (s.vlans || []).map((v, i) => h("tr", {}, h("td", {}, h("input", { name: `id_${i}`, value: v.id, style: { width: "60px" } })), h("td", {}, h("input", { name: `name_${i}`, value: v.name })), h("td", {}, h("input", { name: `subnet_${i}`, value: v.subnet || "", placeholder: "192.168.10.0/24" })), h("td", {}, h("input", { name: `gateway_${i}`, value: v.gateway || "" })), h("td", {}, h("select", { name: `color_${i}` }, ["control", "audio", "light", "video", "mgmt", "trunk", "ap", "wan", "unused"].map((c) => h("option", { value: c, selected: v.color === c }, c)))), h("td", {}, h("input", { type: "checkbox", name: `monitor_${i}`, checked: v.monitor !== false && !!v.subnet })), h("td", {}, h("input", { type: "checkbox", name: `isolated_${i}`, checked: !!v.isolated })), h("td", {}, h("input", { type: "checkbox", name: `remove_${i}` }))));
  const count = (s.vlans || []).length;
  return h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); const d = formData(e.target); const vlans = []; for (let i = 0; i <= count; i++) { if (d[`remove_${i}`]) continue; if (!d[`id_${i}`]) continue; vlans.push({ id: d[`id_${i}`], name: d[`name_${i}`], subnet: d[`subnet_${i}`], gateway: d[`gateway_${i}`], color: d[`color_${i}`], monitor: !!d[`monitor_${i}`], isolated: !!d[`isolated_${i}`] }); } saveSettings("vlans", vlans, e.target); } },
    h("div", { class: "table-wrap" }, h("table", { class: "ports-editor" }, h("thead", {}, h("tr", {}, h("th", {}, "ID"), h("th", {}, "Name"), h("th", {}, "Subnet"), h("th", {}, "Gateway"), h("th", {}, "Colour"), h("th", {}, "Monitor"), h("th", {}, "Isolated"), h("th", {}, "Remove"))), h("tbody", {}, rows, h("tr", {}, h("td", {}, h("input", { name: `id_${count}`, placeholder: "new", style: { width: "60px" } })), h("td", {}, h("input", { name: `name_${count}` })), h("td", {}, h("input", { name: `subnet_${count}` })), h("td", {}, h("input", { name: `gateway_${count}` })), h("td", {}, h("select", { name: `color_${count}` }, ["unused", "control", "audio", "light", "video", "mgmt", "trunk", "ap", "wan"].map((c) => h("option", { value: c }, c)))), h("td", {}, h("input", { type: "checkbox", name: `monitor_${count}` })), h("td", {}, h("input", { type: "checkbox", name: `isolated_${count}` })), h("td", {}))))),
    h("div", { class: "form-note" }, "Isolated VLANs (e.g. 90 WAN_PASS) have no subnet, are never scanned and must not appear on normal trunks."),
    h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save VLANs")),
  );
}
function policyForm(s) {
  const p = s.policy || {};
  return h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); const d = formData(e.target); saveSettings("policy", { trunk_vlans: d.trunk_vlans.split(/[,\s]+/).filter(Boolean), forbidden_vlans: d.forbidden_vlans.split(/[,\s]+/).filter(Boolean), forbidden_trunk_vlans: d.forbidden_trunk_vlans.split(/[,\s]+/).filter(Boolean), deprecated_names: d.deprecated_names, ap_trunk: { native_vlan: d.ap_native, tagged_vlans: d.ap_tagged.split(/[,\s]+/).filter(Boolean) }, rstp_root_switch: d.rstp_root_switch, dante_switches: d.dante_switches, dante_vlan: d.dante_vlan }, e.target); } },
    h("div", { class: "form-row" }, textField("Trunk VLANs", "trunk_vlans", (p.trunk_vlans || []).join(", ")), textField("Forbidden VLANs (anywhere)", "forbidden_vlans", (p.forbidden_vlans || []).join(", ")), textField("Forbidden on trunks", "forbidden_trunk_vlans", (p.forbidden_trunk_vlans || []).join(", ")), textField("Deprecated names", "deprecated_names", (p.deprecated_names || []).join(", "))),
    h("div", { class: "form-row" }, numField("AP trunk native VLAN", "ap_native", p.ap_trunk?.native_vlan ?? 99), textField("AP trunk tagged VLANs", "ap_tagged", (p.ap_trunk?.tagged_vlans || []).join(", ")), selectField("Expected RSTP root", "rstp_root_switch", p.rstp_root_switch, s.switches.map((sw) => [sw.id, sw.name])), textField("Dante switches", "dante_switches", (p.dante_switches || []).join(", ")), numField("Dante VLAN", "dante_vlan", p.dante_vlan ?? 20)),
    h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save policy")),
  );
}
function profilesForm(s) {
  const profiles = s.port_profiles || {};
  const rows = Object.values(profiles).map((p) => h("tr", {}, h("td", { class: "mono" }, p.name), h("td", {}, h("select", { name: `type_${p.name}` }, ["access", "trunk", "ap_trunk", "wan_pass", "unused"].map((t) => h("option", { value: t, selected: p.type === t }, t)))), h("td", {}, h("input", { name: `vlan_${p.name}`, value: p.vlan ?? "", style: { width: "60px" } })), h("td", {}, h("input", { name: `tagged_${p.name}`, value: (p.tagged || []).join(",") })), h("td", {}, h("input", { name: `native_${p.name}`, value: p.native ?? "", style: { width: "60px" } })), h("td", {}, h("select", { name: `color_${p.name}` }, ["control", "audio", "light", "video", "mgmt", "trunk", "ap", "wan", "unused"].map((c) => h("option", { value: c, selected: p.color === c }, c)))), h("td", {}, h("input", { name: `label_${p.name}`, value: p.label || "" }))));
  return h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); const d = formData(e.target); const out = {}; Object.keys(profiles).forEach((name) => { out[name] = { type: d[`type_${name}`], vlan: d[`vlan_${name}`], tagged: d[`tagged_${name}`].split(/[,\s]+/).filter(Boolean), native: d[`native_${name}`], color: d[`color_${name}`], label: d[`label_${name}`] }; }); if (d.new_name) out[d.new_name.toUpperCase()] = { type: d.new_type, vlan: d.new_vlan, tagged: d.new_tagged.split(/[,\s]+/).filter(Boolean), native: d.new_native, color: d.new_color, label: d.new_name.toUpperCase() }; saveSettings("port_profiles", out, e.target); } },
    h("div", { class: "table-wrap" }, h("table", { class: "ports-editor" }, h("thead", {}, h("tr", {}, h("th", {}, "Profile"), h("th", {}, "Type"), h("th", {}, "VLAN"), h("th", {}, "Tagged"), h("th", {}, "Native"), h("th", {}, "Colour"), h("th", {}, "Label"))), h("tbody", {}, rows, h("tr", {}, h("td", {}, h("input", { name: "new_name", placeholder: "NEW" })), h("td", {}, h("select", { name: "new_type" }, ["access", "trunk", "ap_trunk", "wan_pass", "unused"].map((t) => h("option", { value: t }, t)))), h("td", {}, h("input", { name: "new_vlan", style: { width: "60px" } })), h("td", {}, h("input", { name: "new_tagged" })), h("td", {}, h("input", { name: "new_native", style: { width: "60px" } })), h("td", {}, h("select", { name: "new_color" }, ["unused", "control", "audio", "light", "video", "mgmt", "trunk", "ap", "wan"].map((c) => h("option", { value: c }, c)))), h("td", {}))))),
    h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save profiles")),
  );
}
function inventoryForm(s) {
  const list = s.inventory || [];
  const rows = list.map((d, i) => h("tr", {}, h("td", {}, h("input", { name: `name_${i}`, value: d.name || "" })), h("td", {}, h("input", { name: `ip_${i}`, value: d.ip_address || "" })), h("td", {}, h("input", { name: `mac_${i}`, value: d.mac_address || "" })), h("td", {}, h("input", { name: `vlan_${i}`, value: d.vlan ?? "", style: { width: "60px" } })), h("td", {}, h("input", { name: `role_${i}`, value: d.role || "" })), h("td", {}, h("input", { name: `owner_${i}`, value: d.owner || "" })), h("td", {}, h("input", { type: "checkbox", name: `remove_${i}` }))));
  const n = list.length;
  return h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); const d = formData(e.target); const out = []; for (let i = 0; i <= n; i++) { if (d[`remove_${i}`] || (!d[`ip_${i}`] && !d[`mac_${i}`])) continue; out.push({ id: list[i]?.id, name: d[`name_${i}`], ip_address: d[`ip_${i}`], mac_address: d[`mac_${i}`], vlan: d[`vlan_${i}`], role: d[`role_${i}`], owner: d[`owner_${i}`], expected: true }); } saveSettings("inventory", out, e.target); } },
    h("div", { class: "form-note" }, "Expected end devices (consoles, media servers, …). An inventory device that goes offline raises a warning. Names entered via the Devices page are stored separately by MAC."),
    h("div", { class: "table-wrap" }, h("table", { class: "ports-editor" }, h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "IP"), h("th", {}, "MAC"), h("th", {}, "VLAN"), h("th", {}, "Role"), h("th", {}, "Owner"), h("th", {}, "Remove"))), h("tbody", {}, rows, h("tr", {}, h("td", {}, h("input", { name: `name_${n}`, placeholder: "new device" })), h("td", {}, h("input", { name: `ip_${n}` })), h("td", {}, h("input", { name: `mac_${n}` })), h("td", {}, h("input", { name: `vlan_${n}`, style: { width: "60px" } })), h("td", {}, h("input", { name: `role_${n}` })), h("td", {}, h("input", { name: `owner_${n}` })), h("td", {}))))),
    h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save inventory")),
  );
}
function internetForm(s) {
  const probes = s.internet?.probes || [];
  const n = probes.length;
  const rows = probes.map((p, i) => h("tr", {}, h("td", {}, h("input", { name: `name_${i}`, value: p.name || "" })), h("td", {}, h("select", { name: `type_${i}` }, ["ping", "https", "http"].map((t) => h("option", { value: t, selected: p.type === t }, t)))), h("td", {}, h("input", { name: `target_${i}`, value: p.target || "" })), h("td", {}, h("input", { type: "checkbox", name: `remove_${i}` }))));
  return h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); const d = formData(e.target); const out = []; for (let i = 0; i <= n; i++) { if (d[`remove_${i}`] || !d[`target_${i}`]) continue; out.push({ name: d[`name_${i}`], type: d[`type_${i}`], target: d[`target_${i}`] }); } saveSettings("internet", { probes: out }, e.target); } },
    h("div", { class: "table-wrap" }, h("table", { class: "ports-editor" }, h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Type"), h("th", {}, "Target"), h("th", {}, "Remove"))), h("tbody", {}, rows, h("tr", {}, h("td", {}, h("input", { name: `name_${n}`, placeholder: "new probe" })), h("td", {}, h("select", { name: `type_${n}` }, ["ping", "https", "http"].map((t) => h("option", { value: t }, t)))), h("td", {}, h("input", { name: `target_${n}` })), h("td", {}))))),
    h("div", { class: "form-actions" }, h("button", { type: "submit", class: "btn btn-primary btn-sm" }, "Save probes")),
  );
}

// ----------------------------------------------------------------------------- boot
function boot() {
  document.getElementById("btnRefresh").addEventListener("click", () => requestScan("fast"));
  document.getElementById("btnFullScan").addEventListener("click", () => requestScan("full"));
  document.getElementById("drawerBackdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.drawer) closeDrawer(); });
  window.addEventListener("hashchange", render);
  if (!location.hash) location.hash = "#/overview";
  render();
  fetchState().then(connectEvents);
  setInterval(tickClock, 1000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) fetchState(); });
}
boot();

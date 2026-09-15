# ABOUTUS Network Monitor

Read-only local production-network dashboard for the ABOUTUS show network.

## Current v1.0 scope

- FastAPI backend with a static browser dashboard.
- Editable network configuration in `config/network.yaml`.
- Modern dark-default dashboard with a top-navigation web app layout.
- Overview, Devices, Topology, and Settings views with responsive spacing and reduced visual noise.
- Persisted UI state for active page, device filters, VLAN collapse state, expanded device details, and dashboard settings.
- Live status checks for:
  - LANCOM router
  - FOH Allied Telesis switch
  - one or more stage switches
  - Internet probes
  - VLAN gateways
- Manual inventory loaded from config and merged with safe subnet discovery.
- Devices grouped by VLAN with search, VLAN/status/source/type filters, explicit expand icons, and grouped detail views.
- Device details for identity, network, location, services, history, and manual notes.
- Compact Overview topology plus full Topology view for Internet, router, switches, VLAN lanes, infrastructure details, proven port locations, and unmapped devices.
- Switches tab with clickable front-panel style port views for FOH and Stage switches.
- Manual switch-port layouts, roles, expected VLANs, PVID/native VLANs, and notes in `config/network.yaml`.
- SQLite-backed device history, first-seen/last-seen continuity, and recent status-change events.
- Overview operator panels for problem devices, recent events, and quick device filters.
- Open-web-interface actions for infrastructure and devices with IP addresses.
- Optional read-only SNMP polling for infrastructure uptime, interface state, speed, traffic counters, error counters, and MAC forwarding observations.
- VLAN-aware Q-BRIDGE-MIB polling for FOH MAC-to-port learning.
- Switch/port details display `Unknown` unless explicitly present in inventory or proven by trusted SNMP edge-port mapping.

No router or switch configuration is changed. No packet capture or deep traffic inspection is included.

## Repository structure

```text
aboutus-monitor    One-file setup, run, service, logs, and health helper
app/
  checks.py          Probe helpers for ping, TCP, and HTTP checks
  config.py          YAML config loader
  devices.py         Device identity, location, and state correlation
  discovery.py       Safe fping/nmap ping-sweep discovery collector
  main.py            FastAPI app and routes
  monitor.py         Shared scheduler, scan lock, cache, and event bus
  problems.py        Problem detection and acknowledgement state
  settings.py        Editable settings API helpers
  snmp.py            Optional read-only SNMP collector
  storage.py         SQLite persistence for snapshots, device state, and events
  switches.py        Switch faceplate and port-detail view builder
  topology.py        Topology graph builder
  static/            Browser UI assets
config/
  network.yaml       Editable network and inventory config
data/
  monitor.sqlite3    Local generated history database, ignored by git
```

## Run locally on the Raspberry Pi

```bash
cd /home/aboutus/aboutus-network-monitor
./aboutus-monitor setup
./aboutus-monitor run
```

Then open:

```text
http://192.168.99.10:8080
```

## Reverse proxy URL

The dashboard can also be served through local nginx on port `80`, so browsers do not need `:8080`.

- Direct app URL: `http://192.168.99.10:8080`
- Pretty URL via nginx: `http://aboutus-net`
- Alternate local URL: `http://aboutus-net.intern`
- IP URL via nginx: `http://192.168.99.10`

nginx listens on port `80` and reverse-proxies requests to the app on `127.0.0.1:8080`. This is a local HTTP reverse proxy only; HTTPS is not configured.

Install or update the nginx proxy config:

```bash
cd /home/aboutus/aboutus-network-monitor
./aboutus-monitor install-nginx
```

Check and reload nginx manually:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

The nginx site installed by the helper is:

```nginx
server {
    listen 80;
    server_name aboutus-net aboutus-net.intern 192.168.99.10;

    location / {
        proxy_pass http://127.0.0.1:8080;

        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 5s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }
}
```

## Start on boot

Use the project helper to install the systemd boot service:

```bash
cd /home/aboutus/aboutus-network-monitor
./aboutus-monitor install-service
```

That command creates/updates `/etc/systemd/system/aboutus-network-monitor.service`, enables it, and starts it. It may ask for the Pi user's sudo password.

If the dashboard is already running in the foreground with `./aboutus-monitor run`, stop that process first so port `8080` is free for systemd.

After installation, use the same helper for day-to-day control:

```bash
./aboutus-monitor status
./aboutus-monitor restart
./aboutus-monitor logs
./aboutus-monitor health
./aboutus-monitor history
./aboutus-monitor stop
./aboutus-monitor start
```

Normal app/UI edits do not require reinstalling the service. Use:

- browser hard refresh for static UI/CSS/JS changes;
- `./aboutus-monitor restart` for Python/backend changes;
- `./aboutus-monitor install-service` only when installing for the first time or changing the service definition.

To remove the boot service:

```bash
./aboutus-monitor uninstall-service
```

## API

- `GET /api/state` returns the current complete shared dashboard snapshot.
- `GET /api/events` opens the Server-Sent Events stream for live shared state updates.
- `GET /api/status` returns the complete dashboard snapshot for compatibility.
- `GET /api/devices` returns all known/discovered devices and VLAN groups.
- `GET /api/topology` returns the visual topology payload.
- `GET /api/switches` returns switch faceplate and port detail data.
- `GET /api/history` returns recent device events and persisted snapshot totals.
- `GET /api/scan/status` returns whether the monitor is idle, polling, scanning discovery, or reporting the last error.
- `POST /api/scan/request?mode=fast` requests a lightweight shared poll for known infrastructure, devices, and SNMP switch data.
- `POST /api/scan/request?mode=full` requests a full shared read-only refresh including subnet discovery.
- `GET /api/docs` shows the FastAPI-generated OpenAPI docs.

## UI

The v1.0 frontend is a lightweight static app with no external CDN dependencies. It keeps all data from the backend available, but uses progressive disclosure:

- Overview shows show-ready state, critical cards, problem devices, recent events, DNS checks, compact network path, and compact VLAN cards.
- Devices provides search, filters, grouped VLAN sections, and expandable device details with identity, network, location, services, history, and notes.
- Topology shows the main Internet/router/switch path, infrastructure SNMP details, known port locations, unmapped devices, and VLAN lanes.
- Switches shows clickable physical switch faceplates with per-port roles, VLAN indicators, link state, speed, counters, errors, learned MACs by VLAN, and direct-vs-trunk learning.
- Settings stores dashboard preferences in browser `localStorage`.

The UI uses fixed grid tracks, wrapping controls, and compact action buttons so labels, status pills, and web-interface buttons do not overlap on desktop or tablet-sized screens.

The browser also stores the active page, device filters, collapsed VLAN groups, and expanded device details in `localStorage`, so automatic refreshes do not reset the working view.

The backend owns the monitoring scheduler, shared cache, scan lock, and refresh cooldown. The browser loads `/api/state` once, then subscribes to `/api/events` for live `state_snapshot`, `scan_status`, device, switch, topology, error, and heartbeat events. If SSE is unavailable, the UI falls back to passive state polling only; browsers do not start independent monitoring cycles.

The browser refresh button calls `/api/scan/request?mode=fast`. Any accepted refresh updates the one shared backend scan state, so every open PC, tablet, or phone sees the same scan phase, cooldown, errors, and refreshed data.

## History Database

v1.0 writes a local SQLite database to `data/monitor.sqlite3`. It stores:

- recent dashboard snapshot totals;
- per-device first seen, last seen, last checked, previous status, last status change, and offline-since fields;
- recent device events such as joined, left, proven VLAN moved, IP changed, offline, unknown, and recovered.

The database is local generated state and is ignored by git. Override its location with:

```bash
export ABOUTUS_MONITOR_DB=/path/to/monitor.sqlite3
```

Show recent events from SSH:

```bash
./aboutus-monitor history
```

## Configuration

Edit `config/network.yaml` to add real manual inventory entries under `inventory`. Unknown values should be left blank or set to `Unknown`; the app will not infer switch ports or MAC addresses.

Optional `web_url` fields can be added to infrastructure and inventory entries:

```yaml
inventory:
  - name: "FOH Switch AT-GS950/48"
    ip: "192.168.99.2"
    vlan: "MGMT"
    role: "switch"
    expected: true
    web_url: "http://192.168.99.2"
```

If `web_url` is not configured but an IP address is known, the API exposes a generated default of `http://<ip>`. The UI opens these links in a new browser tab and does not assume HTTPS unless it is configured.

The current `infrastructure` list remains supported. v0.5 also accepts a future object-style shape with multiple stage switches:

```yaml
infrastructure:
  router:
    name: "LANCOM 1783VAW"
    ip: "192.168.99.1"
    role: "router"
    web_url: "http://192.168.99.1"

  foh_switch:
    name: "FOH AT-GS950/48"
    ip: "192.168.99.2"
    role: "core-switch"
    web_url: "http://192.168.99.2"

  stage_switches:
    - name: "Stage TL-SG1016PE"
      ip: "192.168.99.3"
      role: "stage-switch"
      web_url: "http://192.168.99.3"
```

The config path can be overridden with:

```bash
export ABOUTUS_MONITOR_CONFIG=/path/to/network.yaml
```

The helper also reads `/home/aboutus/aboutus-network-monitor/.env` when it exists. Use `.env.example` as the template and keep real secrets out of git.

## Optional SNMP

v1.0 can enrich infrastructure and device information with read-only SNMP. It uses local system tools only:

```bash
sudo apt install snmp
```

SNMP is enabled in `config/network.yaml`, but devices only return data when they allow read-only SNMP from the Pi and the local `.env` contains the correct community. Keep real communities in `.env` and reference them with `community_env` in `config/network.yaml`; do not commit real SNMP communities to git.

Useful environment variables for `.env`:

```dotenv
ABOUTUS_SNMP_COMMUNITY=your-readonly-community
ABOUTUS_SNMP_VERSION=2c
ABOUTUS_SNMP_TIMEOUT_SECONDS=2
ABOUTUS_SNMP_RETRIES=0
ABOUTUS_SNMP_COMMUNITY_DELL=your-dell-readonly-community
ABOUTUS_SNMP_COMMUNITY_EAP650=your-eap650-readonly-community
```

After changing `.env`, restart the service:

```bash
./aboutus-monitor restart
```

The collector reads common system, interface, counter, error, bridge/FDB, Q-BRIDGE, and optional PoE OIDs. It never performs SNMP SET/write operations and does not change switch, router, or access point configuration.

The TP-Link Omada EAP650 access points are configured as first-class `access_points`, not switches:

- `ABOUTUS-AP-FOH` at `192.168.99.30`, location `FOH`, management VLAN `99`.
- `ABOUTUS-AP-STAGE-A` at `192.168.99.31`, location `STAGE A`, management VLAN `99`.

Both EAP650s use `ABOUTUS_SNMP_COMMUNITY_EAP650` from `.env` for read-only SNMP v2c. Their expected AP trunks use VLAN `99` untagged/native for AP management and VLANs `10`, `20`, `30`, `40` tagged for Wi-Fi clients. The dashboard shows an AP uplink as `unconfirmed` until LLDP or MAC-learning evidence proves the exact port.

Manual AP SNMP smoke tests. Quote the community expansion so a `#` inside the value remains part of the SNMP community:

```bash
set -a
. ./.env
set +a
snmpwalk -v2c -c "$ABOUTUS_SNMP_COMMUNITY_EAP650" 192.168.99.30 1.3.6.1.2.1.1
snmpwalk -v2c -c "$ABOUTUS_SNMP_COMMUNITY_EAP650" 192.168.99.31 1.3.6.1.2.1.1
```

MAC forwarding tables are not always exact device locations because switches also learn MACs on uplinks. For that reason, SNMP MAC observations are only promoted to a connected switch/port when the configured port profile and topology indicate the MAC is on an edge/access path.

```yaml
switches:
  - id: dell-foh
    snmp: {version: 2c, community_env: ABOUTUS_SNMP_COMMUNITY_DELL}
    ports:
      "21": {profile: MGMT, label: PI}
      "23": {profile: TRUNK, label: ROUTER, neighbor: lancom-router}
```

Without enough port/topology evidence, the dashboard still shows SNMP uptime and port/counter data, but device switch/port remains `Unknown`.

The switches use Q-BRIDGE-MIB where available for VLAN-aware MAC learning:

```text
1.3.6.1.2.1.17.7.1.2.2.1.2
```

The index is interpreted as `VLAN ID + MAC bytes`. Trunk/downstream ports such as Dell FOH ports `23`, `27`, and `28`, or Allied port `48`, are shown as learned-through paths, not direct device locations.

The Switches tab also decodes Q-BRIDGE VLAN metadata:

```text
1.3.6.1.2.1.17.7.1.4.3.1.1  VLAN names
1.3.6.1.2.1.17.7.1.4.3.1.2  VLAN egress/member port bitmap
1.3.6.1.2.1.17.7.1.4.3.1.4  VLAN untagged port bitmap
1.3.6.1.2.1.17.7.1.4.5.1.1  Port PVID/native VLAN
```

Port bitmaps are decoded into physical port numbers. Device names are matched from known MAC addresses, local ARP neighbor data, and the Pi's own interface MAC where available; unresolved MACs remain visible as MAC addresses with unknown device/IP.

## Switch Layouts

Switch faceplates are configured under `switches` in `config/network.yaml`. Each switch can define:

- port count and grid layout;
- per-port label and role;
- access/trunk/downstream/management type;
- expected VLANs;
- PVID/native VLAN;
- uplink/trunk flag;
- notes.

The current layout includes:

- Dell FOH port `21`: ABOUTUS Monitor Pi MGMT access.
- Dell FOH port `22`: `ABOUTUS-AP-FOH` AP trunk (native VLAN 99, tagged 10/20/30/40).
- Dell FOH port `23`: LANCOM router trunk.
- Dell FOH ports `27` and `28`: stage/downstream trunks.
- Dell Stage A port `22`: `ABOUTUS-AP-STAGE-A` AP trunk (native VLAN 99, tagged 10/20/30/40).
- Dell Stage A port `28`: uplink to FOH.
- Allied AT-GS950/48 port `48`: optional auxiliary native-99 trunk.
- TP-Link T1600G port `23`: expansion uplink to FOH.

## VLAN Colors

The UI color coding is driven by VLAN `color` keys and `port_profiles` in `config/network.yaml`. These colors are used by VLAN cards, topology VLAN nodes, device VLAN groups, and switch port faceplates.

Current VLAN palette:

- VLAN `10` CONTROL: blue
- VLAN `20` AUDIO: green
- VLAN `30` LIGHT: red
- VLAN `40` VIDEO: purple
- VLAN `90` WAN_PASS: orange
- VLAN `99` MGMT: light/white

Trunk and AP trunk colors come from the configured `TRUNK` and `AP_TRUNK` port profiles.

## Discovery

v1.0 can run a read-only `fping` sweep across all configured VLAN subnets, with `nmap -sn` as a fallback. The collector:

- uses no port scanning;
- scans only subnets listed in `config/network.yaml`;
- applies a per-subnet timeout;
- merges discovered IPs with manual inventory by IP address;
- lets manual inventory names and expected-device metadata win;
- keeps MAC, switch, and port fields as `Unknown` when not proven.

If `fping`/`nmap` is missing, fails, or a subnet is unreachable, the API still returns a dashboard payload with discovery warnings.

Devices must answer the current read-only host-discovery method to appear automatically. If a client joins a VLAN but blocks ping/host-discovery probes, it may not show up until router ARP/DHCP or SNMP correlation is added.

Install discovery tools on Raspberry Pi OS if needed:

```bash
sudo apt install fping nmap
```

## Next milestones

1. Add DHCP lease correlation so clients that block ping can still be seen earlier.
2. Add vendor/OUI lookup from a local bundled OUI database for MAC addresses not identified by SNMP or ARP.
3. Add per-device availability percentages and a short status timeline.
4. Add alert mute controls for expected maintenance windows.
5. Add browser-based smoke tests for the static UI.

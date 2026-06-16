# ABOUTUS Network Monitor

Read-only local production-network dashboard for the ABOUTUS show network.

## Current v0.8 scope

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
  discovery.py       Safe nmap ping-sweep discovery collector
  main.py            FastAPI app and routes
  snmp.py            Optional read-only SNMP collector
  storage.py         SQLite persistence for snapshots, device state, and events
  status_service.py  Builds the dashboard status payload
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

- `GET /api/status` returns the complete dashboard snapshot.
- `GET /api/devices` returns all known/discovered devices and VLAN groups.
- `GET /api/topology` returns the visual topology payload.
- `GET /api/switches` returns switch faceplate and port detail data.
- `GET /api/history` returns recent device events and persisted snapshot totals.
- `GET /api/docs` shows the FastAPI-generated OpenAPI docs.

## UI

The v0.8 frontend is a lightweight static app with no external CDN dependencies. It keeps all data from the backend available, but uses progressive disclosure:

- Overview shows show-ready state, critical cards, problem devices, recent events, DNS checks, compact network path, and compact VLAN cards.
- Devices provides search, filters, grouped VLAN sections, and expandable device details with identity, network, location, services, history, and notes.
- Topology shows the main Internet/router/switch path, infrastructure SNMP details, known port locations, unmapped devices, and VLAN lanes.
- Switches shows clickable physical switch faceplates with per-port roles, VLAN indicators, link state, speed, counters, errors, learned MACs by VLAN, and direct-vs-trunk learning.
- Settings stores dashboard preferences in browser `localStorage`.

The UI uses fixed grid tracks, wrapping controls, and compact action buttons so labels, status pills, and web-interface buttons do not overlap on desktop or tablet-sized screens.

The browser also stores the active page, device filters, collapsed VLAN groups, and expanded device details in `localStorage`, so automatic refreshes do not reset the working view.

The browser refresh button and automatic dashboard refresh call `/api/status?refresh=true`, which bypasses the backend cache and runs a fresh read-only discovery pass. This keeps Recent Events useful for device join/leave changes.

## History Database

v0.8 writes a local SQLite database to `data/monitor.sqlite3`. It stores:

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

v0.8 can enrich infrastructure and device information with read-only SNMP. It uses local system tools only:

```bash
sudo apt install snmp
```

SNMP is enabled in `config/network.yaml`, but devices only return data when they allow read-only SNMP from the Pi and the local `.env` contains the correct community. Do not commit real SNMP communities to git.

Useful environment variables for `.env`:

```dotenv
ABOUTUS_SNMP_COMMUNITY=your-readonly-community
ABOUTUS_SNMP_VERSION=2c
ABOUTUS_SNMP_TIMEOUT_SECONDS=2
ABOUTUS_SNMP_RETRIES=0
```

After changing `.env`, restart the service:

```bash
./aboutus-monitor restart
```

The collector reads common system, interface, counter, error, bridge/FDB, Q-BRIDGE, and optional PoE OIDs. It does not write SNMP values and does not change switch or router configuration.

MAC forwarding tables are not always exact device locations because switches also learn MACs on uplinks. For that reason, SNMP MAC observations are only promoted to a connected switch/port when the config marks the matching switch port as a trusted edge port:

```yaml
snmp:
  enabled: true
  trusted_edge_ports:
    stage-switch:
      - "1"
      - "2"
  uplink_ports:
    stage-switch:
      - "16"
```

Without trusted edge-port config, the dashboard still shows SNMP uptime and port/counter data, but device switch/port remains `Unknown`.

The FOH Allied Telesis AT-GS950/48 uses Q-BRIDGE-MIB for VLAN-aware MAC learning:

```text
1.3.6.1.2.1.17.7.1.2.2.1.2
```

The index is interpreted as `VLAN ID + MAC bytes`. Trunk/downstream ports such as FOH port `47` and `41` are shown as learned-through ports, not direct device locations.

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

- FOH port `47`: LANCOM router trunk.
- FOH port `41`: stage/downstream trunk.
- FOH port `48`: ABOUTUS Monitor Pi MGMT access.
- Stage port `15`: FOH uplink, manual fallback.
- Stage port `16`: MGMT access, manual fallback.

## VLAN Colors

The UI color coding is configurable in `config/network.yaml` under `ui.vlan_colors` and `ui.port_role_colors`. These colors are used by VLAN cards, topology VLAN nodes, device VLAN groups, and switch port faceplates.

Current VLAN palette:

- VLAN `10` CONTROL: blue
- VLAN `20` AUDIO: green
- VLAN `30` LASER: red
- VLAN `40` LIGHTING: purple
- VLAN `50` VIDEO: orange
- VLAN `99` MGMT: light/white

Stage and router trunk colors are configured separately under `ui.port_role_colors`.

## Discovery

v0.8 can run a read-only `nmap -sn` ping sweep across all configured VLAN subnets. The collector:

- uses no port scanning;
- scans only subnets listed in `config/network.yaml`;
- applies a per-subnet timeout;
- merges discovered IPs with manual inventory by IP address;
- lets manual inventory names and expected-device metadata win;
- keeps MAC, switch, and port fields as `Unknown` when not proven.

If `nmap` is missing, fails, or a subnet is unreachable, the API still returns a dashboard payload with discovery warnings.

Devices must answer the current read-only host-discovery method to appear automatically. If a client joins a VLAN but blocks ping/host-discovery probes, it may not show up until router ARP/DHCP or SNMP correlation is added.

Install nmap on Raspberry Pi OS if needed:

```bash
sudo apt install nmap
```

## Next milestones

1. Add router ARP/DHCP lease correlation so clients that block ping can still be seen.
2. Add vendor/OUI lookup from a local bundled OUI database for MAC addresses not identified by nmap.
3. Add editable device notes and role/type overrides from the UI, persisted back to inventory.
4. Add per-device availability percentages and a short status timeline.
5. Add per-port historical counter deltas so busy/erroring switch ports stand out during a show.
4. Add alert acknowledgement/mute controls for expected maintenance windows.

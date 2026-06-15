# ABOUTUS Network Monitor

Read-only local production-network dashboard for the ABOUTUS show network.

## Current v0.5 scope

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
- Devices grouped by VLAN with search, VLAN/status/source/type filters, explicit expand icons, and expandable details.
- Compact Overview topology plus full Topology view for Internet, router, switches, VLAN lanes, and warning state.
- Open-web-interface actions for infrastructure and devices with IP addresses.
- Switch/port details display `Unknown` unless explicitly present in inventory or proven by a future collector.

No router or switch configuration is changed. No packet capture or deep traffic inspection is included.

## Repository structure

```text
app/
  checks.py          Probe helpers for ping, TCP, and HTTP checks
  config.py          YAML config loader
  discovery.py       Safe nmap ping-sweep discovery collector
  main.py            FastAPI app and routes
  status_service.py  Builds the dashboard status payload
  static/            Browser UI assets
config/
  network.yaml       Editable network and inventory config
```

## Run locally on the Raspberry Pi

```bash
cd /home/aboutus/aboutus-network-monitor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Then open:

```text
http://192.168.99.10:8080
```

## API

- `GET /api/status` returns the complete dashboard snapshot.
- `GET /api/devices` returns all known/discovered devices and VLAN groups.
- `GET /api/topology` returns the visual topology payload.
- `GET /api/docs` shows the FastAPI-generated OpenAPI docs.

## UI

The v0.5 frontend is a lightweight static app with no external CDN dependencies. It keeps all data from the backend available, but uses progressive disclosure:

- Overview shows show-ready state, critical cards, warnings, compact network path, and compact VLAN cards.
- Devices provides search, filters, grouped VLAN sections, and expandable device details with visible chevron affordances.
- Topology shows the main Internet/router/switch path plus VLAN lanes.
- Settings stores dashboard preferences in browser `localStorage`.

The UI uses fixed grid tracks, wrapping controls, and compact action buttons so labels, status pills, and web-interface buttons do not overlap on desktop or tablet-sized screens.

The browser also stores the active page, device filters, collapsed VLAN groups, and expanded device details in `localStorage`, so automatic refreshes do not reset the working view.

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

## Discovery

v0.5 can run a read-only `nmap -sn` ping sweep across all configured VLAN subnets. The collector:

- uses no port scanning;
- scans only subnets listed in `config/network.yaml`;
- applies a per-subnet timeout;
- merges discovered IPs with manual inventory by IP address;
- lets manual inventory names and expected-device metadata win;
- keeps MAC, switch, and port fields as `Unknown` when not proven.

If `nmap` is missing, fails, or a subnet is unreachable, the API still returns a dashboard payload with discovery warnings.

Install nmap on Raspberry Pi OS if needed:

```bash
sudo apt install nmap
```

## Next milestones

1. Add SQLite persistence for inventory snapshots and last-seen history.
2. Add SNMP polling for real switch uptime, port state, speed, counters, errors, and bridge/MAC forwarding tables.
3. Correlate IP to MAC using router ARP/DHCP/SNMP where possible.
4. Correlate discovered MAC addresses with forwarding tables to show exact switch/port when proven.

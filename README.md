# ABOUTUS Network Monitor

Read-only local production-network dashboard for the ABOUTUS show network.

## Current v0.3 scope

- FastAPI backend with a static browser dashboard.
- Editable network configuration in `config/network.yaml`.
- Modern dark-default dashboard with Overview, Devices, Topology, and Settings views.
- Persisted UI settings for System/Dark/Light theme, refresh interval, compact mode, and device visibility.
- Live status checks for:
  - LANCOM router
  - FOH Allied Telesis switch
  - Stage TP-Link switch
  - Internet probes
  - VLAN gateways
- Manual inventory loaded from config and merged with safe subnet discovery.
- Devices grouped by VLAN with search, VLAN/status/source/type filters, and expandable details.
- Clean topology overview for Internet, router, switches, VLAN lanes, and warning state.
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

The v0.3 frontend is a lightweight static app with no external CDN dependencies. It keeps all data from the backend available, but uses progressive disclosure:

- Overview shows show-ready state, critical cards, warnings, and compact VLAN cards.
- Devices provides search, filters, grouped VLAN sections, and expandable device details.
- Topology shows the main Internet/router/switch path plus VLAN lanes.
- Settings stores dashboard preferences in browser `localStorage`.

## Configuration

Edit `config/network.yaml` to add real manual inventory entries under `inventory`. Unknown values should be left blank or set to `Unknown`; the app will not infer switch ports or MAC addresses.

The config path can be overridden with:

```bash
export ABOUTUS_MONITOR_CONFIG=/path/to/network.yaml
```

## Discovery

v0.2 can run a read-only `nmap -sn` ping sweep across all configured VLAN subnets. The collector:

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

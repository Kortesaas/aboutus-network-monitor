# ABOUTUS Network Monitor

Read-only local production-network dashboard for the ABOUTUS show network.

## Current v0.1 scope

- FastAPI backend with a static browser dashboard.
- Editable network configuration in `config/network.yaml`.
- Live status checks for:
  - LANCOM router
  - FOH Allied Telesis switch
  - Stage TP-Link switch
  - Internet probes
  - VLAN gateways
- Static/manual device inventory loaded from config.
- Switch/port details display `Unknown` unless explicitly present in inventory.

No router or switch configuration is changed. No packet capture or deep traffic inspection is included.

## Repository structure

```text
app/
  checks.py          Probe helpers for ping, TCP, and HTTP checks
  config.py          YAML config loader
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

## Configuration

Edit `config/network.yaml` to add real manual inventory entries. Unknown values should be left blank or set to `Unknown`; the app will not infer switch ports or MAC addresses.

The config path can be overridden with:

```bash
export ABOUTUS_MONITOR_CONFIG=/path/to/network.yaml
```

## Next milestones

1. Add SQLite persistence for inventory snapshots and last-seen history.
2. Add subnet discovery using ARP/nmap-style collectors where appropriate.
3. Add SNMP polling for real switch uptime, port state, speed, counters, errors, and bridge/MAC forwarding tables.
4. Correlate discovered MAC addresses with forwarding tables to show exact switch/port when proven.

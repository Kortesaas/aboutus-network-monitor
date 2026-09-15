"""Device correlation and presence classification.

Every cycle the monitor collects independent observations (ping results,
discovery sweeps, router/Linux ARP tables, switch MAC tables, LLDP) and this
module folds them into one device record per identity. Identity is the
normalised MAC address (with metadata aliases merged), falling back to the IP
address when no MAC is known.

Presence states:

``active_located``   current confirmation + exact switch/port/VLAN
``active_unlocated`` current confirmation, but no edge port could be proven
``mac_only``         only seen in switch MAC tables, never with an IP (hidden)
``relocating``       recently disappeared from a known port, waiting to reappear
``stale``            no current confirmation; last seen within the offline window
``offline``          not seen for longer than the offline window
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Any

from .neighbors import normalize_mac


ACTIVE_LOCATED = "active_located"
ACTIVE_UNLOCATED = "active_unlocated"
MAC_ONLY = "mac_only"
RELOCATING = "relocating"
STALE = "stale"
OFFLINE = "offline"

STATE_LABELS = {
    ACTIVE_LOCATED: "Active · located",
    ACTIVE_UNLOCATED: "Active · unlocated",
    MAC_ONLY: "MAC only",
    RELOCATING: "Relocating",
    STALE: "Stale",
    OFFLINE: "Offline",
}

# Small built-in OUI table for vendors common on a show network. Extend via
# ``ui.oui`` in the configuration if needed.
OUI_VENDORS = {
    "e4:5f:01": "Raspberry Pi", "dc:a6:32": "Raspberry Pi", "b8:27:eb": "Raspberry Pi", "d8:3a:dd": "Raspberry Pi", "28:cd:c1": "Raspberry Pi", "2c:cf:67": "Raspberry Pi",
    "68:4f:64": "Dell", "f8:b1:56": "Dell", "d4:be:d9": "Dell", "18:66:da": "Dell", "e4:54:e8": "Dell", "b0:7b:25": "Dell",
    "98:de:d0": "TP-Link", "e8:48:b8": "TP-Link", "50:c7:bf": "TP-Link", "b0:be:76": "TP-Link", "30:de:4b": "TP-Link", "1c:61:b4": "TP-Link", "60:32:b1": "TP-Link", "9c:53:22": "TP-Link", "a8:42:a1": "TP-Link", "5c:e9:31": "TP-Link",
    "00:a0:57": "LANCOM", "e0:1a:ea": "Allied Telesis", "00:09:41": "Allied Telesis", "ec:cd:6d": "Allied Telesis",
    "00:1d:c1": "Audinate (Dante)", "00:a0:de": "Yamaha", "00:0e:dd": "Shure", "00:04:f2": "Polycom", "00:e0:4c": "Realtek",
    "00:0f:e5": "Mercury/Behringer", "00:23:8b": "Quanta", "d8:5e:d3": "GIGA-BYTE", "30:f9:47": "Sagemcom", "74:da:38": "Edimax",
    "00:50:c2": "IEEE registration", "70:b3:d5": "IEEE registration", "00:0c:29": "VMware", "00:15:5d": "Hyper-V", "08:00:27": "VirtualBox",
    "a4:83:e7": "Apple", "f0:18:98": "Apple", "3c:22:fb": "Apple", "bc:d0:74": "Apple", "f4:5c:89": "Apple", "8c:85:90": "Apple", "d0:81:7a": "Apple", "14:7d:da": "Apple", "a8:51:ab": "Apple", "d4:57:63": "Apple",
    "00:1b:21": "Intel", "3c:e9:f7": "Intel", "48:51:c5": "Intel", "8c:8c:aa": "Intel", "a0:36:9f": "Intel", "b4:96:91": "Intel", "dc:41:a9": "Intel", "00:1e:c9": "Dell", "c8:f7:50": "Dell",
    "00:11:32": "Synology", "90:09:d0": "Synology", "00:04:4b": "NVIDIA", "b4:2e:99": "GIGA-BYTE", "1c:1b:0d": "GIGA-BYTE", "e0:d5:5e": "GIGA-BYTE",
    "00:26:37": "Samsung", "8c:f5:a3": "Samsung", "ac:5f:3e": "Samsung", "00:e0:63": "Cabletron", "00:1c:f0": "D-Link", "c4:a8:1d": "D-Link", "b8:69:f4": "Routerboard/MikroTik", "cc:2d:e0": "MikroTik",
    "00:14:d1": "TRENDnet", "00:0a:e6": "Elitegroup", "00:1a:6b": "Ubiquiti", "24:5a:4c": "Ubiquiti", "68:d7:9a": "Ubiquiti", "fc:ec:da": "Ubiquiti", "78:8a:20": "Ubiquiti", "e0:63:da": "Ubiquiti",
    "00:1e:8f": "Canon", "00:80:92": "Silex", "00:1a:4b": "HP", "3c:d9:2b": "HP", "9c:8e:99": "HP", "00:21:5a": "HP", "b0:5c:da": "HP", "00:25:b3": "HP", "10:1f:74": "HP",
    "00:26:b9": "Dell", "00:0d:56": "Dell", "00:1f:f3": "Apple", "e8:06:88": "Apple", "00:0b:82": "Grandstream", "00:e0:4b": "Jump Industrielle", "00:1c:2e": "HPN Supply Chain",
    "00:19:cb": "ZyXEL", "00:13:49": "ZyXEL", "b8:ec:a3": "ZyXEL", "00:80:e1": "STMicro", "00:04:a3": "Microchip", "d8:80:39": "Microchip", "54:10:ec": "Microchip",
    "00:1d:0f": "Panasonic", "04:20:9a": "Panasonic", "8c:c1:21": "Panasonic", "00:80:f0": "Panasonic", "00:10:83": "HP", "00:e0:2b": "Extreme", "00:04:96": "Extreme",
    "00:50:56": "VMware", "00:1c:42": "Parallels", "00:03:93": "Apple", "00:0d:93": "Apple", "00:16:cb": "Apple", "00:17:f2": "Apple", "00:19:e3": "Apple", "00:1b:63": "Apple",
    "00:60:2f": "Cisco", "00:1e:13": "Cisco", "00:1b:d4": "Cisco", "f4:cf:e2": "Cisco", "00:07:0e": "Cisco", "00:1a:a1": "Cisco", "5c:5e:ab": "Juniper", "00:05:85": "Juniper",
    "38:2c:4a": "ASUSTek", "1c:87:2c": "ASUSTek", "2c:fd:a1": "ASUSTek", "04:d4:c4": "ASUSTek", "00:1f:c6": "ASUSTek", "e0:3f:49": "ASUSTek", "74:d0:2b": "ASUSTek", "70:4d:7b": "ASUSTek", "fc:34:97": "ASUSTek",
    "00:1e:06": "WIBRAIN", "00:21:6a": "Intel", "00:24:d7": "Intel", "00:26:c7": "Intel", "10:0b:a9": "Intel", "34:02:86": "Intel", "60:67:20": "Intel", "7c:7a:91": "Intel", "94:e6:f7": "Intel", "b4:6b:fc": "Intel",
    "00:20:6b": "Konica Minolta", "00:11:24": "Apple", "00:14:51": "Apple", "00:1d:4f": "Apple", "00:1e:52": "Apple", "00:1f:5b": "Apple", "00:21:e9": "Apple", "00:22:41": "Apple", "00:23:12": "Apple", "00:23:32": "Apple",
    "00:0a:9c": "Server Technology", "00:0c:c8": "Xytronix", "00:0e:c6": "Asix", "00:12:34": "Camille Bauer", "00:1b:1b": "Siemens", "00:0e:8c": "Siemens", "28:63:36": "Siemens", "00:50:c7": "Fast Corp",
    "00:15:c5": "Dell", "00:1a:a0": "Dell", "00:21:70": "Dell", "00:24:e8": "Dell", "14:18:77": "Dell", "24:b6:fd": "Dell", "5c:f9:dd": "Dell", "74:86:7a": "Dell", "84:2b:2b": "Dell", "b8:ac:6f": "Dell", "bc:30:5b": "Dell", "d0:67:e5": "Dell", "f0:1f:af": "Dell", "f8:bc:12": "Dell", "a4:bb:6d": "Dell", "8c:ec:4b": "Dell", "4c:d9:8f": "Dell", "e4:b9:7a": "Dell", "98:90:96": "Dell", "c8:1f:66": "Dell", "34:17:eb": "Dell", "54:bf:64": "Dell", "6c:2b:59": "Dell", "b0:83:fe": "Dell", "10:98:36": "Dell", "18:a9:9b": "Dell", "20:47:47": "Dell", "28:f1:0e": "Dell", "30:d0:42": "Dell", "4c:76:25": "Dell", "50:9a:4c": "Dell", "78:2b:cb": "Dell", "a0:d3:c1": "Dell", "d4:81:d7": "Dell", "e0:db:55": "Dell",
    "00:e0:4d": "Internet Initiative Japan", "00:0d:b9": "PC Engines", "d8:47:32": "TP-Link", "c0:06:c3": "TP-Link", "14:cc:20": "TP-Link", "18:d6:c7": "TP-Link", "b0:4e:26": "TP-Link", "6c:5a:b0": "TP-Link", "70:4f:57": "TP-Link", "ec:08:6b": "TP-Link", "f4:f2:6d": "TP-Link", "10:fe:ed": "TP-Link", "c4:6e:1f": "TP-Link", "00:31:92": "TP-Link", "5c:62:8b": "TP-Link", "48:22:54": "TP-Link", "ac:15:a2": "TP-Link", "78:8c:b5": "TP-Link", "3c:52:a1": "TP-Link", "a4:2b:b0": "TP-Link", "cc:32:e5": "TP-Link", "d0:37:45": "TP-Link", "e4:c3:2a": "TP-Link", "34:60:f9": "TP-Link", "40:ae:30": "TP-Link", "48:0e:ec": "TP-Link", "9c:a2:f4": "TP-Link", "7c:c2:c6": "TP-Link", "74:fe:ce": "TP-Link", "b4:b0:24": "TP-Link", "00:1d:0f": "Panasonic",
}


def vendor_for_mac(mac: str | None, extra: dict[str, str] | None = None) -> str:
    if not mac:
        return ""
    prefix = mac[:8].lower()
    if extra and prefix in extra:
        return str(extra[prefix])
    return OUI_VENDORS.get(prefix, "")


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def seconds_between(later: Any, earlier: Any) -> float | None:
    a = parse_time(later)
    b = parse_time(earlier)
    if not a or not b:
        return None
    return max(0.0, (a - b).total_seconds())


def _latest(*values: Any) -> str | None:
    best: str | None = None
    best_dt: datetime | None = None
    for value in values:
        parsed = parse_time(value)
        if parsed and (best_dt is None or parsed > best_dt):
            best, best_dt = str(value), parsed
    return best


def vlan_for_ip(ip_address: str | None, networks: list[tuple[Any, dict[str, Any]]]) -> dict[str, Any] | None:
    if not ip_address:
        return None
    try:
        parsed = ipaddress.ip_address(ip_address)
    except ValueError:
        return None
    for network, vlan in networks:
        if parsed in network:
            return vlan
    return None


def vlan_networks(vlans: list[dict[str, Any]]) -> list[tuple[Any, dict[str, Any]]]:
    result = []
    for vlan in vlans:
        if vlan.get("subnet"):
            try:
                result.append((ipaddress.ip_network(str(vlan["subnet"]), strict=False), vlan))
            except ValueError:
                continue
    return result


# --------------------------------------------------------------------------- engine


class DeviceEngine:
    """Builds the device list for one cycle from the collected observations."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.vlans = config["vlans"]
        self.networks = vlan_networks(self.vlans)
        self.vlan_by_id = {vlan["id"]: vlan for vlan in self.vlans}
        self.thresholds = config["monitoring"]
        self.switches = {switch["id"]: switch for switch in config["switches"]}
        self.depth = self._switch_depth()
        self.oui_extra = {str(key).lower(): str(value) for key, value in ((config["raw"].get("ui") or {}).get("oui") or {}).items()}

    # -- helpers -----------------------------------------------------------

    def _switch_depth(self) -> dict[str, int]:
        core = self.config["policy"].get("rstp_root_switch")
        neighbors: dict[str, set[str]] = {sid: set() for sid in self.switches}
        for switch in self.switches.values():
            for port in switch["ports"].values():
                target = port.get("neighbor")
                if target in self.switches:
                    neighbors[switch["id"]].add(target)
                    neighbors[target].add(switch["id"])
        depth: dict[str, int] = {}
        if core in self.switches:
            queue = [core]
            depth[core] = 0
            while queue:
                current = queue.pop(0)
                for other in neighbors[current]:
                    if other not in depth:
                        depth[other] = depth[current] + 1
                        queue.append(other)
        for sid in self.switches:
            depth.setdefault(sid, 1 if self.switches[sid].get("role") != "core" else 0)
        return depth

    def _port_config(self, switch_id: str, port: int | None) -> dict[str, Any]:
        switch = self.switches.get(switch_id)
        if not switch or port is None:
            return {"number": port, "profile": "UNUSED", "type": "unused", "label": "", "configured": False}
        return switch["ports"].get(port) or {"number": port, "profile": "UNUSED", "type": "unused", "label": "", "configured": False}

    def _in_monitored_subnet(self, ip_address: str | None) -> bool:
        return vlan_for_ip(ip_address, self.networks) is not None

    # -- main ----------------------------------------------------------------

    def build(
        self,
        *,
        now: str,
        infra: list[dict[str, Any]],
        snmp_devices: dict[str, dict[str, Any]],
        neighbors: dict[str, dict[str, Any]],
        discovery_hosts: list[dict[str, Any]],
        ping: dict[str, dict[str, Any]],
        previous: dict[str, dict[str, Any]],
        metadata: dict[str, dict[str, Any]],
        inventory: list[dict[str, Any]],
    ) -> dict[str, Any]:
        infra_by_ip = {item["ip_address"]: item for item in infra if item.get("ip_address")}
        router = next((item for item in infra if item.get("kind") == "router"), None)
        gateway_ips = {vlan["gateway"] for vlan in self.vlans if vlan.get("gateway")}
        if router:
            for gateway in gateway_ips:
                infra_by_ip.setdefault(gateway, router)
        infra_macs: dict[str, str] = {}
        for item in infra:
            if item.get("mac_address"):
                infra_macs[item["mac_address"]] = item["id"]

        # IP -> MAC observations ------------------------------------------------
        ip_mac: dict[str, dict[str, Any]] = {}

        def observe(ip_address: str | None, mac: str | None, source: str, seen_at: str, current: bool) -> None:
            mac = normalize_mac(mac)
            if not ip_address or not mac or not self._in_monitored_subnet(ip_address):
                return
            existing = ip_mac.get(ip_address)
            if existing is None or (current and not existing["current"]) or (current == existing["current"] and (parse_time(seen_at) or datetime.min.replace(tzinfo=timezone.utc)) >= (parse_time(existing["seen_at"]) or datetime.min.replace(tzinfo=timezone.utc))):
                ip_mac[ip_address] = {"ip_address": ip_address, "mac_address": mac, "source": source, "seen_at": seen_at, "current": current}
            if ip_address in infra_by_ip:
                infra_macs.setdefault(mac, infra_by_ip[ip_address]["id"])

        for entry in neighbors.values():
            observe(entry["ip_address"], entry["mac_address"], entry["source"], entry.get("seen_at") or now, bool(entry.get("current")))
        for result in snmp_devices.values():
            if result.get("status") != "ok":
                continue
            for entry in result.get("arp") or []:
                observe(entry["ip_address"], entry["mac_address"], "router_arp", result.get("last_poll") or now, True)
            for interface in (result.get("interfaces") or {}).values():
                if interface.get("mac_address"):
                    infra_macs.setdefault(interface["mac_address"], result["id"])
            bridge_mac = (result.get("stp") or {}).get("bridge_mac")
            if bridge_mac:
                infra_macs.setdefault(bridge_mac, result["id"])
        for host in discovery_hosts:
            if host.get("mac_address"):
                observe(host["ip_address"], host["mac_address"], "discovery", host.get("seen_at") or now, False)
        for record in previous.values():
            if record.get("ip_address") and record.get("mac_address") and record["ip_address"] not in ip_mac:
                observe(record["ip_address"], record["mac_address"], "history", record.get("last_seen") or record.get("updated_at") or "", False)

        # LLDP neighbours by chassis / port MAC (for naming and infra detection)
        lldp_by_mac: dict[str, dict[str, Any]] = {}
        infra_names = {str(item.get("hostname") or "").lower(): item["id"] for item in infra if item.get("hostname")}
        infra_names.update({str(item.get("name") or "").lower(): item["id"] for item in infra if item.get("name")})
        for result in snmp_devices.values():
            for port, neighbor in (result.get("lldp") or {}).items():
                for candidate in (neighbor.get("chassis_mac"), normalize_mac(neighbor.get("port_id")), normalize_mac(neighbor.get("chassis_id"))):
                    if candidate:
                        lldp_by_mac[candidate] = {**neighbor, "switch_id": result["id"], "port": port}
                        sys_name = str(neighbor.get("system_name") or "").lower()
                        if sys_name and sys_name in infra_names:
                            infra_macs.setdefault(candidate, infra_names[sys_name])

        # MAC -> FDB observations -------------------------------------------------
        fdb_by_mac: dict[str, list[dict[str, Any]]] = {}
        for result in snmp_devices.values():
            if result.get("status") != "ok" or result.get("kind") not in {"switch", "ap"}:
                continue
            interfaces_by_port = {interface.get("port"): interface for interface in (result.get("interfaces") or {}).values()}
            for entry in result.get("fdb") or []:
                port_config = self._port_config(result["id"], entry.get("port"))
                interface = interfaces_by_port.get(entry.get("port")) or {}
                fdb_by_mac.setdefault(entry["mac_address"], []).append(
                    {
                        "switch_id": result["id"],
                        "switch_name": self.switches.get(result["id"], {}).get("name") or result["id"],
                        "port": entry.get("port"),
                        "port_label": port_config.get("label") or "",
                        "port_type": port_config.get("type"),
                        "port_profile": port_config.get("profile"),
                        "vlan_id": entry.get("vlan_id"),
                        "oper_status": interface.get("oper_status") or "unknown",
                        "depth": self.depth.get(result["id"], 1),
                        "seen_at": result.get("last_poll") or now,
                    }
                )

        # Metadata identity resolution ------------------------------------------
        def identity_for(macs: list[str], ip_address: str | None) -> str:
            for mac in macs:
                meta = metadata.get(f"mac:{mac}")
                if meta:
                    return meta["identity_key"]
            if macs:
                return f"mac:{macs[0]}"
            return f"ip:{ip_address}"

        # Candidate identities -------------------------------------------------------
        candidates: dict[str, dict[str, Any]] = {}

        def candidate(key: str) -> dict[str, Any]:
            return candidates.setdefault(key, {"key": key, "macs": [], "ips": [], "sources": set(), "seen": [], "infra_id": None, "inventory": None, "discovery": None, "ping": None})

        def add_mac(entry: dict[str, Any], mac: str | None) -> None:
            mac = normalize_mac(mac)
            if mac and mac not in entry["macs"]:
                entry["macs"].append(mac)

        def add_ip(entry: dict[str, Any], ip_address: str | None) -> None:
            if ip_address and ip_address not in entry["ips"] and self._in_monitored_subnet(ip_address):
                entry["ips"].append(ip_address)

        # infrastructure first so they claim their MACs
        for item in infra:
            macs = [mac for mac, infra_id in infra_macs.items() if infra_id == item["id"]]
            entry = candidate(f"infra:{item['id']}")
            entry["infra_id"] = item["id"]
            entry["infra"] = item
            for mac in macs:
                add_mac(entry, mac)
            add_ip(entry, item.get("ip_address"))
            entry["sources"].add("infrastructure")
        mac_owner = {mac: f"infra:{infra_id}" for mac, infra_id in infra_macs.items()}
        mac_owner_ip: dict[str, str] = {}

        def key_for(macs: list[str], ip_address: str | None) -> str:
            for mac in macs:
                if mac in mac_owner:
                    return mac_owner[mac]
            key = identity_for(macs, ip_address)
            for mac in macs:
                mac_owner.setdefault(mac, key)
            return key

        for ip_address, item in infra_by_ip.items():
            entry = candidate(f"infra:{item['id']}")
            add_ip(entry, ip_address)
            mac_owner_ip[ip_address] = f"infra:{item['id']}"

        for ip_address, observation in ip_mac.items():
            key = mac_owner_ip.get(ip_address) or key_for([observation["mac_address"]], ip_address)
            entry = candidate(key)
            add_mac(entry, observation["mac_address"])
            add_ip(entry, ip_address)
            entry["sources"].add(observation["source"])
            if observation["current"]:
                entry["seen"].append((observation["seen_at"], observation["source"]))
        for host in discovery_hosts:
            ip_address = host.get("ip_address")
            mac = normalize_mac(host.get("mac_address")) or (ip_mac.get(ip_address) or {}).get("mac_address")
            key = mac_owner_ip.get(ip_address) or key_for([mac] if mac else [], ip_address)
            entry = candidate(key)
            add_mac(entry, mac)
            add_ip(entry, ip_address)
            entry["sources"].add("discovery")
            entry["discovery"] = host
            entry["seen"].append((host.get("seen_at") or now, "discovery"))
        for mac, observations in fdb_by_mac.items():
            key = key_for([mac], None)
            entry = candidate(key)
            add_mac(entry, mac)
            entry["sources"].add("fdb")
        for item in inventory:
            mac = normalize_mac(item.get("mac_address"))
            key = mac_owner_ip.get(item.get("ip_address") or "") or key_for([mac] if mac else [], item.get("ip_address"))
            entry = candidate(key)
            add_mac(entry, mac)
            add_ip(entry, item.get("ip_address"))
            entry["inventory"] = item
            entry["sources"].add("inventory")
        for key, record in previous.items():
            if key.startswith("infra:"):
                continue
            entry = candidates.get(key)
            if entry is None:
                macs = [normalize_mac(record.get("mac_address"))] if normalize_mac(record.get("mac_address")) else []
                resolved = mac_owner_ip.get(record.get("ip_address") or "") or (key_for(macs, record.get("ip_address")) if (macs or record.get("ip_address")) else key)
                if resolved.startswith("infra:"):
                    continue
                entry = candidate(resolved)
                for mac in record.get("mac_addresses") or macs:
                    add_mac(entry, mac)
                add_ip(entry, record.get("ip_address"))
            entry["sources"].add("history")

        # Merge metadata aliases: candidates sharing a metadata identity collapse.
        merged: dict[str, dict[str, Any]] = {}
        for key, entry in candidates.items():
            target = key
            if not key.startswith("infra:"):
                for mac in entry["macs"]:
                    meta = metadata.get(f"mac:{mac}")
                    if meta:
                        target = meta["identity_key"]
                        break
            bucket = merged.get(target)
            if bucket is None:
                merged[target] = {**entry, "key": target, "macs": list(entry["macs"]), "ips": list(entry["ips"]), "sources": set(entry["sources"]), "seen": list(entry["seen"])}
            else:
                for mac in entry["macs"]:
                    add_mac(bucket, mac)
                for ip_address in entry["ips"]:
                    add_ip(bucket, ip_address)
                bucket["sources"].update(entry["sources"])
                bucket["seen"].extend(entry["seen"])
                for field in ("infra_id", "infra", "inventory", "discovery"):
                    bucket[field] = bucket.get(field) or entry.get(field)

        devices: list[dict[str, Any]] = []
        mac_only: list[dict[str, Any]] = []
        for key, entry in merged.items():
            device = self._build_device(entry, now=now, ip_mac=ip_mac, fdb_by_mac=fdb_by_mac, lldp_by_mac=lldp_by_mac, ping=ping, previous=previous.get(key) or previous.get(entry.get("key") or ""), metadata=metadata, snmp_devices=snmp_devices)
            if device is None:
                continue
            if device["state"] == MAC_ONLY:
                mac_only.append(device)
            else:
                devices.append(device)

        devices.sort(key=lambda item: (item.get("vlan_id") or 9999, _ip_key(item.get("ip_address")), item.get("display_name") or ""))
        mac_only.sort(key=lambda item: (item.get("vlan_id") or 9999, item.get("mac_address") or ""))
        return {"devices": devices, "mac_only": mac_only, "infra_macs": infra_macs, "fdb_by_mac": fdb_by_mac, "ip_mac": ip_mac}

    # -- one device ---------------------------------------------------------------

    def _best_location(self, observations: list[dict[str, Any]], infra: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Return (edge location, path observation).

        End devices: the deepest trunk observation is the closest hint to where
        they hang. Infrastructure: the observation on the *upstream* switch
        (smallest depth that is still above the device) is its uplink port.
        """
        edge = [obs for obs in observations if obs.get("port_type") in {"access", "unused", "wan_pass"} and obs.get("port") is not None]
        path = [obs for obs in observations if obs.get("port") is not None and obs not in edge]

        def rank(obs: dict[str, Any]) -> tuple[int, int, int, str]:
            return (1 if obs.get("oper_status") == "up" else 0, 1 if obs.get("port_type") == "access" else 0, obs.get("depth", 0), obs.get("seen_at") or "")

        edge.sort(key=rank, reverse=True)
        if infra and infra.get("kind") in {"switch", "router", "ap"}:
            own_depth = self.depth.get(infra["id"], 99) if infra.get("kind") == "switch" else (-1 if infra.get("kind") == "router" else 99)
            if infra.get("kind") == "router":
                path = [obs for obs in path if obs.get("depth", 0) == 0]
            else:
                path = [obs for obs in path if obs.get("depth", 0) < own_depth]
            path.sort(key=lambda obs: (obs.get("depth", 0), 1 if obs.get("oper_status") == "up" else 0), reverse=True)
        else:
            path.sort(key=lambda obs: (obs.get("depth", 0), 1 if obs.get("oper_status") == "up" else 0), reverse=True)
        return (edge[0] if edge else None), (path[0] if path else None)

    def _build_device(
        self,
        entry: dict[str, Any],
        *,
        now: str,
        ip_mac: dict[str, dict[str, Any]],
        fdb_by_mac: dict[str, list[dict[str, Any]]],
        lldp_by_mac: dict[str, dict[str, Any]],
        ping: dict[str, dict[str, Any]],
        previous: dict[str, Any] | None,
        metadata: dict[str, dict[str, Any]],
        snmp_devices: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        previous = previous or {}
        macs = list(entry["macs"])
        meta = None
        for mac in macs:
            meta = metadata.get(f"mac:{mac}")
            if meta:
                break
        if meta:
            for mac in meta["mac_addresses"]:
                if mac not in macs:
                    macs.append(mac)
        infra = entry.get("infra")
        inventory = entry.get("inventory")

        # current IP: the freshest current observation, else any known IP
        current_ips = sorted(entry["ips"], key=lambda ip: ((ip_mac.get(ip) or {}).get("current", False), (ip_mac.get(ip) or {}).get("seen_at") or ""), reverse=True)
        ip_address = infra.get("ip_address") if infra else (current_ips[0] if current_ips else None)
        if ip_address and not self._in_monitored_subnet(ip_address) and not infra:
            ip_address = None
        if infra and infra.get("ip_address") and not self._in_monitored_subnet(infra["ip_address"]):
            ip_address = infra["ip_address"]
        primary_mac = (ip_mac.get(ip_address) or {}).get("mac_address") if ip_address else None
        if primary_mac and primary_mac not in macs:
            macs.insert(0, primary_mac)
        elif primary_mac:
            macs.remove(primary_mac)
            macs.insert(0, primary_mac)
        mac_address = macs[0] if macs else None

        # FDB observations for any of the MACs
        observations: list[dict[str, Any]] = []
        for mac in macs:
            observations.extend(fdb_by_mac.get(mac) or [])
        edge, path = self._best_location(observations, infra)
        fdb_now = bool(observations)
        fdb_up_now = any(obs.get("oper_status") == "up" for obs in observations)

        # confirmations
        ping_result = ping.get(ip_address) if ip_address else None
        if infra:
            ping_result = infra.get("check") or ping_result
        ping_ok = bool(ping_result and ping_result.get("status") == "online")
        ping_failed = bool(ping_result and ping_result.get("status") == "offline")
        discovery = entry.get("discovery")
        discovery_age = seconds_between(now, discovery.get("seen_at")) if discovery else None
        discovery_recent = discovery_age is not None and discovery_age <= self.thresholds["stale_after_seconds"]
        arp_now = any(current for current in [((ip_mac.get(ip) or {}).get("current", False)) for ip in ([ip_address] if ip_address else [])])
        snmp_ok = bool(infra and (snmp_devices.get(infra["id"]) or {}).get("status") == "ok")

        confirmed_by: list[str] = []
        if ping_ok:
            confirmed_by.append(str((ping_result or {}).get("source") or "ping"))
        if snmp_ok:
            confirmed_by.append("snmp")
        if discovery_recent:
            confirmed_by.append("discovery")
        if fdb_now:
            confirmed_by.append("switch_mac_table")
        if arp_now and not ping_failed:
            confirmed_by.append((ip_mac.get(ip_address) or {}).get("source") or "arp")
        confirmed_now = ping_ok or snmp_ok or discovery_recent or fdb_up_now or (arp_now and not ping_failed) or (fdb_now and not ping_failed)

        # last seen
        seen_candidates = [time for time, _source in entry.get("seen") or []]
        if confirmed_now:
            seen_candidates.append(now)
        if discovery:
            seen_candidates.append(discovery.get("seen_at"))
        seen_candidates.append(previous.get("last_seen"))
        last_seen = _latest(*seen_candidates)
        first_seen = previous.get("first_seen") or (now if confirmed_now or discovery else last_seen or now)
        age = seconds_between(now, last_seen) if last_seen else None

        # VLAN
        vlan = vlan_for_ip(ip_address, self.networks)
        if vlan is None and edge and edge.get("vlan_id") in self.vlan_by_id:
            vlan = self.vlan_by_id[edge["vlan_id"]]
        if vlan is None and edge:
            port_config = self._port_config(edge["switch_id"], edge["port"])
            if port_config.get("vlan") in self.vlan_by_id:
                vlan = self.vlan_by_id[port_config["vlan"]]
        if vlan is None and infra:
            vlan = self.vlan_by_id.get(infra.get("vlan"))

        has_ip = bool(ip_address)
        located = edge is not None and has_ip and vlan is not None
        if infra and not located and confirmed_now and (path is not None or infra.get("kind") in {"switch", "router"}):
            located = True
        previous_state = previous.get("state")
        previously_located = bool(previous.get("switch_id") and previous.get("port"))

        if not has_ip and macs and not inventory and not infra:
            state = MAC_ONLY
            if not fdb_now and not previous_state:
                return None
        elif confirmed_now:
            state = ACTIVE_LOCATED if located else ACTIVE_UNLOCATED
        elif age is None:
            state = OFFLINE
        elif previously_located and age <= self.thresholds["relocating_seconds"] and previous_state in {ACTIVE_LOCATED, RELOCATING}:
            state = RELOCATING
        elif age <= self.thresholds["offline_after_seconds"]:
            state = STALE
        else:
            state = OFFLINE

        # stale-but-still-in-FDB devices are not "current"; keep the last known location for context
        if state == MAC_ONLY and not fdb_now:
            state = OFFLINE

        location = None
        if edge:
            port_config = self._port_config(edge["switch_id"], edge["port"])
            location = {
                "switch_id": edge["switch_id"],
                "switch_name": edge["switch_name"],
                "port": edge["port"],
                "port_label": port_config.get("label") or edge.get("port_label") or "",
                "port_profile": port_config.get("profile"),
                "port_type": port_config.get("type"),
                "vlan_id": edge.get("vlan_id") or port_config.get("vlan"),
                "port_status": edge.get("oper_status"),
                "confidence": "edge" if port_config.get("type") == "access" else "edge_unconfigured",
                "seen_at": edge.get("seen_at"),
            }
        elif infra and path:
            port_config = self._port_config(path["switch_id"], path["port"])
            location = {
                "switch_id": path["switch_id"],
                "switch_name": path["switch_name"],
                "port": path["port"],
                "port_label": port_config.get("label") or path.get("port_label") or "",
                "port_profile": port_config.get("profile"),
                "port_type": port_config.get("type"),
                "vlan_id": path.get("vlan_id"),
                "port_status": path.get("oper_status"),
                "confidence": "uplink",
                "seen_at": path.get("seen_at"),
            }
        elif state in {RELOCATING, STALE, OFFLINE} and previously_located:
            location = {
                "switch_id": previous.get("switch_id"),
                "switch_name": previous.get("switch_name") or previous.get("switch_id"),
                "port": previous.get("port"),
                "port_label": previous.get("port_label") or "",
                "port_profile": previous.get("port_profile"),
                "port_type": previous.get("port_type"),
                "vlan_id": previous.get("vlan_id"),
                "port_status": "unknown",
                "confidence": "last_known",
                "seen_at": previous.get("last_seen"),
            }
        path_info = None
        if path:
            path_info = {
                "switch_id": path["switch_id"],
                "switch_name": path["switch_name"],
                "port": path["port"],
                "port_label": path.get("port_label") or "",
                "port_type": path.get("port_type"),
                "vlan_id": path.get("vlan_id"),
                "message": f"Seen behind {path['switch_name']} port {path['port']} ({path.get('port_label') or path.get('port_type')})",
            }

        # naming
        hostname = (discovery or {}).get("hostname") or previous.get("hostname") or ""
        lldp_name = ""
        for mac in macs:
            neighbor = lldp_by_mac.get(mac)
            if not neighbor:
                continue
            chassis = str(neighbor.get("chassis_id") or "")
            candidate_name = neighbor.get("system_name") or (chassis if chassis and not normalize_mac(chassis) else "")
            if candidate_name:
                lldp_name = candidate_name
                break
        vendor = (discovery or {}).get("vendor") or vendor_for_mac(mac_address, self.oui_extra) or previous.get("vendor") or ""
        if infra:
            vendor = infra.get("vendor") or vendor
        if meta and meta.get("display_name"):
            display_name, name_source = meta["display_name"], "known_device"
        elif infra:
            display_name, name_source = infra["name"], "infrastructure"
        elif inventory and inventory.get("name"):
            display_name, name_source = inventory["name"], "inventory"
        elif hostname:
            display_name, name_source = hostname, "hostname"
        elif lldp_name:
            display_name, name_source = lldp_name, "lldp"
        elif vendor and mac_address:
            display_name, name_source = f"{vendor} {mac_address[-8:].upper()}", "vendor"
        elif ip_address:
            display_name, name_source = ip_address, "ip"
        else:
            display_name, name_source = (mac_address or entry["key"]).upper(), "mac"

        category = (meta or {}).get("device_type") or (inventory or {}).get("role") or ("infrastructure" if infra else "")
        key = entry["key"]
        return {
            "key": key,
            "identity_key": (meta or {}).get("identity_key") or (f"mac:{mac_address}" if mac_address else None),
            "display_name": display_name,
            "name_source": name_source,
            "hostname": hostname,
            "lldp_name": lldp_name,
            "vendor": vendor,
            "category": category,
            "mac_address": mac_address,
            "mac_addresses": macs,
            "ip_address": ip_address,
            "ip_addresses": entry["ips"],
            "vlan_id": vlan["id"] if vlan else None,
            "vlan_name": vlan["name"] if vlan else None,
            "vlan_color": vlan.get("color") if vlan else None,
            "state": state,
            "state_label": STATE_LABELS.get(state, state),
            "status": "online" if state in {ACTIVE_LOCATED, ACTIVE_UNLOCATED} else ("offline" if state == OFFLINE else "unknown"),
            "location": location,
            "path": path_info,
            "confirmed_by": confirmed_by,
            "sources": sorted(entry["sources"]),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "age_seconds": round(age, 1) if age is not None else None,
            "latency_ms": (ping_result or {}).get("latency_ms"),
            "ping_status": (ping_result or {}).get("status") if ping_result else None,
            "is_infrastructure": bool(infra),
            "infra_id": infra["id"] if infra else None,
            "infra_kind": infra.get("kind") if infra else None,
            "expected": bool(infra) or bool(inventory and inventory.get("expected", True)),
            "inventory": inventory,
            "web_url": (meta or {}).get("web_url") or (infra or {}).get("web_url") or (inventory or {}).get("web_url") or (f"http://{ip_address}" if ip_address else None),
            "metadata": {
                "identity_key": (meta or {}).get("identity_key") or (f"mac:{mac_address}" if mac_address else None),
                "editable": bool(mac_address),
                "saved": bool(meta),
                "display_name": (meta or {}).get("display_name") or "",
                "owner": (meta or {}).get("owner") or "",
                "device_type": (meta or {}).get("device_type") or "",
                "criticality": (meta or {}).get("criticality") or "",
                "asset_tag": (meta or {}).get("asset_tag") or "",
                "notes": (meta or {}).get("notes") or "",
                "favorite": bool((meta or {}).get("favorite")),
                "ignored": bool((meta or {}).get("ignored")),
                "mac_addresses": list((meta or {}).get("mac_addresses") or macs),
                "updated_at": (meta or {}).get("updated_at"),
            },
            "favorite": bool((meta or {}).get("favorite")),
            "ignored": bool((meta or {}).get("ignored")),
            "previous_state": previous_state if previous_state != state else None,
        }


def _ip_key(value: Any) -> tuple[int, int]:
    try:
        return (0, int(ipaddress.ip_address(str(value))))
    except ValueError:
        return (1, 0)


def device_record(device: dict[str, Any]) -> dict[str, Any]:
    """Compact persisted form of a device used as ``previous`` on the next cycle."""
    location = device.get("location") or {}
    return {
        "key": device["key"],
        "first_seen": device.get("first_seen"),
        "last_seen": device.get("last_seen"),
        "last_ip": device.get("ip_address"),
        "last_mac": device.get("mac_address"),
        "last_vlan": device.get("vlan_id"),
        "last_switch": location.get("switch_id"),
        "last_port": location.get("port"),
        "state": device.get("state"),
        "display_name": device.get("display_name"),
        "data": {
            "ip_address": device.get("ip_address"),
            "mac_address": device.get("mac_address"),
            "mac_addresses": device.get("mac_addresses"),
            "hostname": device.get("hostname"),
            "vendor": device.get("vendor"),
            "vlan_id": device.get("vlan_id"),
            "switch_id": location.get("switch_id"),
            "switch_name": location.get("switch_name"),
            "port": location.get("port"),
            "port_label": location.get("port_label"),
            "port_profile": location.get("port_profile"),
            "port_type": location.get("port_type"),
        },
    }

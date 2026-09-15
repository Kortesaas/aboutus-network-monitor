"""Topology graph: expected links from configuration + observed LLDP/FDB evidence."""

from __future__ import annotations

import re
from typing import Any

from .neighbors import normalize_mac


def _resolve_lldp_target(neighbor: dict[str, Any], infra: list[dict[str, Any]], infra_macs: dict[str, str]) -> str | None:
    for candidate in (neighbor.get("chassis_mac"), normalize_mac(neighbor.get("chassis_id")), normalize_mac(neighbor.get("port_id"))):
        if candidate and candidate in infra_macs:
            return infra_macs[candidate]
    sys_name = str(neighbor.get("system_name") or "").strip().lower()
    if sys_name:
        for item in infra:
            if sys_name in {str(item.get("hostname") or "").lower(), str(item.get("name") or "").lower(), str(item.get("sys_name") or "").lower()}:
                return item["id"]
    return None


def build_topology(
    *,
    infra: list[dict[str, Any]],
    switch_views: list[dict[str, Any]],
    snmp_devices: dict[str, dict[str, Any]],
    infra_macs: dict[str, str],
    fdb_by_mac: dict[str, list[dict[str, Any]]],
    devices: list[dict[str, Any]],
    internet: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    infra_by_id = {item["id"]: item for item in infra}
    switch_by_id = {view["id"]: view for view in switch_views}
    router = config.get("router")
    core_id = config["policy"].get("rstp_root_switch")

    nodes: list[dict[str, Any]] = [
        {"id": "internet", "kind": "internet", "name": "Internet", "status": internet.get("status", "unknown"), "depth": 0, "detail": ", ".join(f"{probe['name']}: {probe['status']}" for probe in internet.get("probes") or [])},
    ]
    for item in infra:
        depth = {"router": 1, "switch": 2 if item["id"] == core_id else 3, "ap": 4, "monitor": 4}.get(item.get("kind"), 3)
        view = switch_by_id.get(item["id"]) or {}
        snmp = snmp_devices.get(item["id"]) or {}
        nodes.append(
            {
                "id": item["id"],
                "kind": item.get("kind"),
                "name": item.get("name"),
                "role": item.get("role"),
                "status": item.get("status", "unknown"),
                "planned": bool(item.get("planned")),
                "optional": bool(item.get("optional")),
                "ip_address": item.get("ip_address"),
                "model": item.get("model"),
                "vendor": item.get("vendor"),
                "location": item.get("location"),
                "web_url": item.get("web_url"),
                "snmp_status": snmp.get("status") if snmp else ("disabled" if not item.get("snmp_enabled") else "pending"),
                "uptime": ((snmp.get("sys") or {}).get("uptime")) if snmp else None,
                "sys_name": ((snmp.get("sys") or {}).get("name")) if snmp else None,
                "port_count": view.get("port_count"),
                "active_ports": view.get("active_port_count"),
                "issue_count": view.get("issue_count", 0),
                "depth": depth,
                "is_core": item["id"] == core_id,
            }
        )

    links: dict[str, dict[str, Any]] = {}

    def port_matches(node_id: str, port_value: Any, hint: Any) -> bool:
        """True when ``hint`` (a number or an LLDP port id such as Gi1/0/23) names the same port."""
        if port_value is None or hint is None:
            return True
        if str(port_value).strip().lower() == str(hint).strip().lower():
            return True
        hint_text = str(hint).strip().lower()
        names: set[str] = set()
        numbers: set[str] = set()
        view = switch_by_id.get(node_id)
        if view:
            for port in view["ports"]:
                if str(port["number"]) == str(port_value):
                    names |= {str(port.get("name") or "").lower(), str(port.get("alias") or "").lower()}
                    numbers.add(str(port["number"]))
        for interface in ((snmp_devices.get(node_id) or {}).get("interfaces") or {}).values():
            if str(interface.get("port")) == str(port_value) or str(interface.get("if_index")) == str(port_value) or str(interface.get("name") or "").lower() == str(port_value).lower():
                names |= {str(interface.get("name") or "").lower(), str(interface.get("alias") or "").lower(), str(interface.get("descr") or "").lower()}
                numbers |= {str(interface.get("port")), str(interface.get("if_index"))}
        names.discard("")
        if hint_text in names or hint_text in numbers:
            return True
        tail = re.search(r"(\d+)\s*(?::\s*\w+)?$", hint_text)
        if tail and "/" in hint_text and tail.group(1) in numbers:
            return True
        return False

    def find_link(a: str, a_port: Any, b: str, b_port_hint: Any) -> dict[str, Any] | None:
        for link in links.values():
            if {link["source"], link["target"]} != {a, b}:
                continue
            if link["source"] == a:
                pa, pb = link["source_port"], link["target_port"]
            else:
                pa, pb = link["target_port"], link["source_port"]
            if (pa is None or port_matches(a, pa, a_port)) and (pb is None or port_matches(b, pb, b_port_hint)):
                return link
        return None

    def set_side_port(link: dict[str, Any], node_id: str, port_value: Any) -> None:
        if port_value is None:
            return
        if link["source"] == node_id and link["source_port"] is None:
            link["source_port"] = port_value
        elif link["target"] == node_id and link["target_port"] is None:
            link["target_port"] = port_value

    def apply_port_view(link: dict[str, Any], node_id: str, port: dict[str, Any]) -> None:
        """Merge live port information from one end of the link (never downgrade to unknown)."""
        status = port.get("oper_status") or "unknown"
        if status != "unknown" or link.get("status") in (None, "unknown"):
            link["status"] = status
        if port.get("speed_mbps"):
            link["speed_mbps"] = port.get("speed_mbps")
        if port.get("display_vlans") and (port.get("actual") or not link.get("vlans")):
            link["vlans"] = port.get("display_vlans")
        side = "source" if link["source"] == node_id else "target"
        link[f"{side}_port_label"] = port.get("label")
        health_rank = {"critical": 3, "warning": 2, "info": 1, "ok": 0}
        if health_rank.get(port.get("health"), -1) >= health_rank.get(link.get("port_health"), -1):
            link["port_health"] = port.get("health")
        existing = list(link.get("port_issues") or [])
        for issue in port.get("issues") or []:
            if issue["message"] not in existing:
                existing.append(issue["message"])
        link["port_issues"] = existing

    def link_key(a: str, a_port: Any, b: str, b_port: Any) -> str:
        left = (a, str(a_port or ""))
        right = (b, str(b_port or ""))
        if left > right:
            left, right = right, left
        return f"{left[0]}:{left[1]}|{right[0]}:{right[1]}"

    def upsert(source: str, source_port: Any, target: str, target_port: Any, **fields: Any) -> dict[str, Any]:
        key = link_key(source, source_port, target, target_port)
        link = links.get(key)
        if link is None:
            link = {"id": key, "source": source, "source_port": source_port, "target": target, "target_port": target_port, "expected": False, "observed": [], "issues": [], "status": "unknown", "kind": "link"}
            links[key] = link
        for name, value in fields.items():
            if name == "observed":
                for item in value:
                    if item not in link["observed"]:
                        link["observed"].append(item)
            elif name == "issues":
                link["issues"].extend(value)
            elif value is not None:
                link[name] = value
        return link

    # Internet <-> router
    if router:
        upsert("internet", None, router["id"], "WAN", expected=True, kind="wan", status=internet.get("status", "unknown"), observed=["probe"] if internet.get("status") == "online" else [])

    # Expected links from switch port configuration
    for view in switch_views:
        for port in view["ports"]:
            target = port.get("expected_neighbor")
            if not target or target not in infra_by_id:
                continue
            target_port = None
            target_view = switch_by_id.get(target)
            if target_view:
                for other in target_view["ports"]:
                    if other.get("expected_neighbor") == view["id"]:
                        target_port = other["number"]
                        break
            if target == (router or {}).get("id") and router.get("uplink_port"):
                target_port = router["uplink_port"]
            link = upsert(view["id"], port["number"], target, target_port, expected=True, kind=port.get("type") or "trunk")
            apply_port_view(link, view["id"], port)

    # Observed LLDP links
    for device_id, result in snmp_devices.items():
        if result.get("status") != "ok" or device_id not in infra_by_id:
            continue
        view = switch_by_id.get(device_id)
        for local_port, neighbor in (result.get("lldp") or {}).items():
            target = _resolve_lldp_target(neighbor, infra, infra_macs)
            if not target or target == device_id:
                continue
            port_view = next((port for port in view["ports"] if port["number"] == local_port), None) if view else None
            link = find_link(device_id, local_port, target, neighbor.get("port_id"))
            if link is None:
                # Is there an expected link on this port to somebody else? -> mismatch
                expected_here = next((l for l in links.values() if l.get("expected") and ((l["source"] == device_id and str(l["source_port"]) == str(local_port)) or (l["target"] == device_id and str(l["target_port"]) == str(local_port)))), None)
                link = upsert(device_id, local_port, target, neighbor.get("port_id"), observed=["lldp"], kind=(port_view or {}).get("type") or "link")
                if expected_here:
                    expected_target = expected_here["target"] if expected_here["source"] == device_id else expected_here["source"]
                    message = f"{infra_by_id[device_id]['name']} port {local_port}: LLDP sees {infra_by_id[target]['name']} but {infra_by_id.get(expected_target, {}).get('name', expected_target)} is expected"
                    issue = {"code": "lldp_mismatch", "severity": "critical", "message": message}
                    expected_here["issues"].append(issue)
                    link["issues"].append(issue)
                elif port_view and port_view.get("type") in {"access", "unused"} and infra_by_id[target].get("kind") in {"switch", "router"}:
                    link["issues"].append({"code": "infra_on_access_port", "severity": "warning", "message": f"{infra_by_id[target]['name']} is connected to {infra_by_id[device_id]['name']} port {local_port}, which is not configured as a trunk"})
            else:
                if "lldp" not in link["observed"]:
                    link["observed"].append("lldp")
                set_side_port(link, device_id, local_port)
                set_side_port(link, target, neighbor.get("port_id"))
            link["lldp_remote"] = {"system_name": neighbor.get("system_name"), "port_id": neighbor.get("port_id"), "port_description": neighbor.get("port_description"), "seen_from": device_id}
            if port_view:
                apply_port_view(link, device_id, port_view)

    # Observed FDB evidence for infrastructure MACs (weaker than LLDP)
    for mac, infra_id in infra_macs.items():
        for observation in fdb_by_mac.get(mac) or []:
            switch_id = observation["switch_id"]
            if switch_id == infra_id or switch_id not in infra_by_id or observation.get("port") is None:
                continue
            existing = find_link(switch_id, observation["port"], infra_id, None)
            if existing:
                if "fdb" not in existing["observed"]:
                    existing["observed"].append("fdb")
                continue
            port_type = observation.get("port_type")
            target_kind = infra_by_id[infra_id].get("kind")
            direct = port_type in {"access", "unused", "wan_pass"} or (port_type == "ap_trunk" and target_kind == "ap")
            if not direct:
                continue
            link = upsert(switch_id, observation["port"], infra_id, None, observed=["fdb"], kind=port_type or "access")
            view = switch_by_id.get(switch_id)
            port_view = next((port for port in view["ports"] if port["number"] == observation["port"]), None) if view else None
            if port_view:
                apply_port_view(link, switch_id, port_view)
                if target_kind in {"switch", "router"} and port_type in {"access", "unused"}:
                    link["issues"].append({"code": "infra_on_access_port", "severity": "warning", "message": f"{infra_by_id[infra_id]['name']} is connected to {infra_by_id[switch_id]['name']} port {observation['port']}, which is not configured as a trunk"})

    # Device attachments (edge located devices) as light-weight leaf links
    attachments: dict[str, list[dict[str, Any]]] = {}
    for device in devices:
        location = device.get("location") or {}
        if device.get("is_infrastructure") or not location.get("switch_id") or location.get("confidence") not in {"edge", "edge_unconfigured"}:
            continue
        attachments.setdefault(location["switch_id"], []).append({"key": device["key"], "display_name": device["display_name"], "port": location.get("port"), "vlan_id": device.get("vlan_id"), "state": device.get("state")})

    # Finalise link health
    for link in links.values():
        if link.get("expected") and not link["observed"] and link.get("status") != "up" and link.get("kind") != "wan":
            target = infra_by_id.get(link["target"], {})
            source = infra_by_id.get(link["source"], {})
            severity = "info" if (target.get("optional") or target.get("planned") or source.get("optional") or source.get("planned")) else "critical"
            if not any("uplink" in message.lower() for message in link.get("port_issues") or []):
                link["issues"].append({"code": "expected_link_down", "severity": severity, "message": f"Expected link {source.get('name', link['source'])} ↔ {target.get('name', link['target'])} is not up"})
        severities = {issue["severity"] for issue in link["issues"]}
        if "critical" in severities or link.get("port_health") == "critical":
            link["health"] = "critical"
        elif "warning" in severities or link.get("port_health") == "warning":
            link["health"] = "warning"
        elif link.get("status") == "up" or (link.get("kind") == "wan" and link.get("status") == "online"):
            link["health"] = "ok"
        elif link.get("status") == "down":
            link["health"] = "down"
        else:
            link["health"] = "unknown"

    return {"nodes": nodes, "links": list(links.values()), "attachments": attachments}

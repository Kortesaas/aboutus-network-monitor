"""Switch and port view: merges configuration (expected) with SNMP (actual)."""

from __future__ import annotations

from typing import Any


def _vlan_names(config: dict[str, Any]) -> dict[int, str]:
    return {vlan["id"]: vlan["name"] for vlan in config["vlans"]}


def expected_vlans(port: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Expected membership for a configured port: ``{"tagged": [...], "untagged": [...], "pvid": int|None}``."""
    ptype = port.get("type")
    if ptype == "access" or ptype == "wan_pass":
        vlan = port.get("vlan")
        return {"tagged": [], "untagged": [vlan] if vlan is not None else [], "pvid": vlan}
    if ptype == "trunk":
        return {"tagged": list(port.get("tagged") or policy["trunk_vlans"]), "untagged": [port["native"]] if port.get("native") is not None else [], "pvid": port.get("native")}
    if ptype == "ap_trunk":
        native = port.get("native") if port.get("native") is not None else policy["ap_trunk"]["native_vlan"]
        return {"tagged": list(port.get("tagged") or policy["ap_trunk"]["tagged_vlans"]), "untagged": [native] if native is not None else [], "pvid": native}
    return {"tagged": [], "untagged": [], "pvid": None}


def actual_vlans(port_number: int, vlans: dict[Any, dict[str, Any]], pvids: dict[Any, Any]) -> dict[str, Any]:
    tagged: list[int] = []
    untagged: list[int] = []
    for vlan in vlans.values():
        vlan_id = int(vlan["id"])
        if port_number in (vlan.get("untagged_ports") or []):
            untagged.append(vlan_id)
        elif port_number in (vlan.get("member_ports") or []) or port_number in (vlan.get("tagged_ports") or []):
            tagged.append(vlan_id)
    pvid = pvids.get(port_number)
    if pvid is None:
        pvid = pvids.get(str(port_number))
    return {"tagged": sorted(tagged), "untagged": sorted(untagged), "pvid": int(pvid) if pvid is not None else None, "known": bool(vlans)}


def compare_port(expected: dict[str, Any], actual: dict[str, Any], port: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a list of ``{"code", "severity", "message"}`` issues for one port."""
    issues: list[dict[str, str]] = []
    if not actual.get("known"):
        return issues
    ptype = port.get("type")
    forbidden = set(policy.get("forbidden_vlans") or [])
    forbidden_trunk = set(policy.get("forbidden_trunk_vlans") or [])
    members = set(actual["tagged"]) | set(actual["untagged"])
    if members & forbidden:
        issues.append({"code": "forbidden_vlan", "severity": "critical", "message": f"Port carries forbidden VLAN {', '.join(str(v) for v in sorted(members & forbidden))}"})
    if ptype in {"trunk", "ap_trunk"}:
        exp_tagged = set(expected["tagged"])
        missing = exp_tagged - set(actual["tagged"])
        if ptype == "ap_trunk":
            missing -= set(actual["untagged"])
        if missing:
            issues.append({"code": "trunk_missing_vlan", "severity": "critical" if ptype == "trunk" else "warning", "message": f"Missing tagged VLAN {', '.join(str(v) for v in sorted(missing))}"})
        extra_forbidden = members & forbidden_trunk
        if extra_forbidden:
            issues.append({"code": "trunk_forbidden_vlan", "severity": "critical", "message": f"Trunk wrongly carries VLAN {', '.join(str(v) for v in sorted(extra_forbidden))}"})
        exp_pvid = expected.get("pvid")
        if ptype == "ap_trunk":
            if exp_pvid is not None and exp_pvid not in actual["untagged"]:
                issues.append({"code": "ap_trunk_native", "severity": "warning", "message": f"AP trunk should have VLAN {exp_pvid} untagged/native"})
            if exp_pvid is not None and actual.get("pvid") not in (None, exp_pvid):
                issues.append({"code": "ap_trunk_pvid", "severity": "warning", "message": f"PVID is {actual.get('pvid')}, expected {exp_pvid}"})
        else:
            # A plain trunk should not have a client VLAN untagged.
            client_untagged = [v for v in actual["untagged"] if v in exp_tagged and v != 1]
            if client_untagged and exp_pvid is None:
                issues.append({"code": "trunk_untagged_client_vlan", "severity": "warning", "message": f"VLAN {', '.join(str(v) for v in client_untagged)} is untagged on a trunk"})
    elif ptype == "access":
        vlan = port.get("vlan")
        if vlan is not None:
            if vlan not in actual["untagged"] or (actual.get("pvid") is not None and actual["pvid"] != vlan):
                if vlan in actual["tagged"] and not actual["untagged"]:
                    message = f"VLAN {vlan} is tagged instead of untagged (PVID {actual.get('pvid')})"
                elif actual["untagged"]:
                    message = f"Access port is untagged in VLAN {actual['untagged'][0]}, expected {vlan}"
                else:
                    message = f"Access port has no untagged VLAN (PVID {actual.get('pvid')}), expected {vlan}"
                issues.append({"code": "access_wrong_vlan", "severity": "critical", "message": message})
            tagged_extra = [v for v in actual["tagged"] if v != vlan]
            if tagged_extra:
                issues.append({"code": "access_tagged_vlans", "severity": "warning", "message": f"Access port also carries tagged VLAN {', '.join(str(v) for v in tagged_extra)}"})
    elif ptype == "wan_pass":
        vlan = port.get("vlan")
        others = members - {vlan}
        if others:
            issues.append({"code": "wan_pass_leak", "severity": "critical", "message": f"WAN passthrough port also carries VLAN {', '.join(str(v) for v in sorted(others))}"})
    elif ptype == "unused":
        client = members & set(policy.get("trunk_vlans") or [])
        if len(client) > 1:
            issues.append({"code": "unconfigured_trunk", "severity": "info", "message": f"Unconfigured port carries VLAN {', '.join(str(v) for v in sorted(client))}"})
    return issues


def build_switch_view(
    switch: dict[str, Any],
    infra_status: dict[str, Any],
    snmp_result: dict[str, Any] | None,
    devices_by_port: dict[int, list[dict[str, Any]]],
    infra_by_port: dict[int, list[dict[str, Any]]],
    config: dict[str, Any],
    neighbor_flags: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    policy = config["policy"]
    neighbor_flags = neighbor_flags or {}
    names = _vlan_names(config)
    snmp_ok = bool(snmp_result and snmp_result.get("status") == "ok")
    interfaces_by_port: dict[int, dict[str, Any]] = {}
    if snmp_ok:
        for interface in (snmp_result.get("interfaces") or {}).values():
            if interface.get("port") is not None:
                interfaces_by_port[int(interface["port"])] = interface
    vlans = (snmp_result or {}).get("vlans") or {}
    pvids = (snmp_result or {}).get("pvids") or {}
    lldp = (snmp_result or {}).get("lldp") or {}
    threshold = config["monitoring"].get("port_error_threshold", 1)

    ports: list[dict[str, Any]] = []
    issue_count = 0
    for number in range(1, switch["port_count"] + 1):
        port = switch["ports"].get(number) or {"number": number, "profile": "UNUSED", "type": "unused", "color": "unused", "label": "", "vlan": None, "tagged": [], "native": None, "neighbor": None, "notes": "", "configured": False}
        interface = interfaces_by_port.get(number) or {}
        expected = expected_vlans(port, policy)
        actual = actual_vlans(number, vlans, pvids) if snmp_ok else {"tagged": [], "untagged": [], "pvid": None, "known": False}
        issues = compare_port(expected, actual, port, policy)
        errors_delta = int(interface.get("errors_delta") or 0)
        discards_delta = int(interface.get("discards_delta") or 0)
        if interface and errors_delta >= threshold:
            issues.append({"code": "port_errors", "severity": "warning", "message": f"{errors_delta} new errors since last poll"})
        if interface and discards_delta >= max(threshold, 1) and port.get("type") in {"trunk", "ap_trunk"}:
            issues.append({"code": "port_discards", "severity": "info", "message": f"{discards_delta} new discards since last poll"})
        oper = interface.get("oper_status") or ("unknown" if not snmp_ok else "unknown")
        speed = interface.get("speed_mbps")
        if oper == "up" and speed and port.get("type") in {"trunk", "ap_trunk"} and speed < 1000:
            issues.append({"code": "trunk_speed", "severity": "warning", "message": f"Trunk negotiated only {speed} Mbit/s"})
        if oper == "up" and speed and port.get("type") == "access" and speed < 100:
            issues.append({"code": "access_speed", "severity": "info", "message": f"Link negotiated only {speed} Mbit/s"})
        if oper == "down" and port.get("type") in {"trunk", "ap_trunk"} and port.get("neighbor"):
            neighbor = neighbor_flags.get(port["neighbor"]) or {}
            severity = "info" if (neighbor.get("optional") or neighbor.get("planned") or neighbor.get("enabled") is False) else "critical"
            issues.append({"code": "uplink_down", "severity": severity, "message": f"Expected uplink to {neighbor.get('name') or port['neighbor']} is down"})
        neighbor = lldp.get(number)
        actual_display = actual if actual.get("known") else None
        # Display membership: actual when known, else the expected profile.
        display_untagged = actual["untagged"] if actual.get("known") else expected["untagged"]
        display_tagged = actual["tagged"] if actual.get("known") else expected["tagged"]
        health = "ok"
        if any(issue["severity"] == "critical" for issue in issues):
            health = "critical"
        elif any(issue["severity"] == "warning" for issue in issues):
            health = "warning"
        elif issues:
            health = "info"
        issue_count += len(issues)
        port_devices = devices_by_port.get(number) or []
        port_infra = infra_by_port.get(number) or []
        ports.append(
            {
                "number": number,
                "if_index": interface.get("if_index"),
                "name": interface.get("name") or "",
                "alias": interface.get("alias") or "",
                "label": port.get("label") or "",
                "profile": port.get("profile"),
                "type": port.get("type"),
                "color": port.get("color"),
                "configured": bool(port.get("configured")),
                "sfp": number in (switch.get("sfp_ports") or []),
                "notes": port.get("notes") or "",
                "expected_neighbor": port.get("neighbor"),
                "expected": {"tagged": expected["tagged"], "untagged": expected["untagged"], "pvid": expected["pvid"], "vlan": port.get("vlan")},
                "actual": actual_display,
                "display_vlans": {"tagged": display_tagged, "untagged": display_untagged, "names": {str(v): names.get(v, f"VLAN {v}") for v in set(display_tagged) | set(display_untagged)}},
                "vlan_source": "snmp" if actual.get("known") else "config",
                "oper_status": oper,
                "admin_status": interface.get("admin_status") or "unknown",
                "speed_mbps": speed,
                "in_bps": interface.get("in_bps"),
                "out_bps": interface.get("out_bps"),
                "in_octets": interface.get("in_octets"),
                "out_octets": interface.get("out_octets"),
                "errors": (interface.get("in_errors") or 0) + (interface.get("out_errors") or 0) if interface else None,
                "discards": (interface.get("in_discards") or 0) + (interface.get("out_discards") or 0) if interface else None,
                "errors_delta": errors_delta,
                "discards_delta": discards_delta,
                "lldp": neighbor,
                "devices": [{"key": d["key"], "display_name": d["display_name"], "ip_address": d.get("ip_address"), "mac_address": d.get("mac_address"), "state": d.get("state"), "vlan_id": d.get("vlan_id")} for d in port_devices],
                "infrastructure": port_infra,
                "device_count": len(port_devices),
                "issues": issues,
                "health": health,
            }
        )

    sys_info = (snmp_result or {}).get("sys") or {}
    stp = (snmp_result or {}).get("stp") or {"supported": False}
    qos = (snmp_result or {}).get("qos") or {"supported": False}
    dante = dante_readiness(switch, qos, policy)
    up_ports = [port for port in ports if port["oper_status"] == "up"]
    return {
        "id": switch["id"],
        "name": switch["name"],
        "hostname": switch.get("hostname"),
        "role": switch.get("role"),
        "vendor": switch.get("vendor"),
        "model": switch.get("model"),
        "location": switch.get("location"),
        "ip_address": switch.get("ip_address"),
        "web_url": switch.get("web_url"),
        "optional": bool(switch.get("optional")),
        "enabled": bool(switch.get("enabled", True)),
        "status": infra_status.get("status", "unknown"),
        "latency_ms": (infra_status.get("check") or {}).get("latency_ms"),
        "snmp_status": "ok" if snmp_ok else ((snmp_result or {}).get("status") or "pending"),
        "snmp_error": (snmp_result or {}).get("error"),
        "snmp_warnings": ((snmp_result or {}).get("errors") or [])[:5],
        "last_poll": (snmp_result or {}).get("last_poll"),
        "poll_duration_ms": (snmp_result or {}).get("duration_ms"),
        "static_polled_at": (snmp_result or {}).get("static_polled_at"),
        "vendor_profile": (snmp_result or {}).get("vendor_profile"),
        "sys_name": sys_info.get("name") or "",
        "sys_descr": sys_info.get("descr") or "",
        "sys_location": sys_info.get("location") or "",
        "uptime": sys_info.get("uptime") or "",
        "uptime_seconds": sys_info.get("uptime_seconds"),
        "port_count": switch["port_count"],
        "layout": switch.get("layout") or {"columns": 14, "rows": 2},
        "sfp_ports": switch.get("sfp_ports") or [],
        "active_port_count": len(up_ports),
        "issue_count": issue_count,
        "error_count": sum(int(port.get("errors") or 0) for port in ports),
        "discard_count": sum(int(port.get("discards") or 0) for port in ports),
        "vlans": sorted(({"id": int(v["id"]), "name": v.get("name"), "tagged_ports": v.get("tagged_ports") or [], "untagged_ports": v.get("untagged_ports") or [], "member_ports": v.get("member_ports") or []} for v in vlans.values()), key=lambda item: item["id"]),
        "stp": stp,
        "qos": qos,
        "dante": dante,
        "rstp_root_expected": bool(switch.get("rstp_root_expected")),
        "dante_capable": bool(switch.get("dante_capable")),
        "ports": ports,
    }


def dante_readiness(switch: dict[str, Any], qos: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the Dante QoS recommendation (DSCP 56 highest, 46 high, 8 low) where detectable."""
    if not switch.get("dante_capable") and switch["id"] not in (policy.get("dante_switches") or []):
        return {"relevant": False, "status": "n/a", "message": "Not a Dante switch"}
    if not qos.get("supported"):
        return {"relevant": True, "status": "unknown", "message": "QoS configuration is not readable via SNMP on this switch; verify DSCP 56/46 priority manually."}
    dscp = {int(k): int(v) for k, v in (qos.get("dscp_map") or {}).items()}
    if not dscp:
        return {"relevant": True, "status": "unknown", "message": "No DSCP map returned."}
    q56 = dscp.get(56)
    q46 = dscp.get(46)
    q8 = dscp.get(8)
    other = [queue for code, queue in dscp.items() if code not in {56, 46, 8}]
    top_other = max(other) if other else 0
    trust = qos.get("trust_mode") or {}
    trusted = [port for port, mode in trust.items() if mode == "dscp"]
    problems = []
    if q56 is None or q46 is None:
        problems.append("DSCP 56/46 not mapped")
    else:
        if q56 <= top_other:
            problems.append(f"DSCP 56 (Dante clock) is in queue {q56}, not above default traffic (queue {top_other})")
        if q46 <= top_other:
            problems.append(f"DSCP 46 (Dante audio) is in queue {q46}, not above default traffic (queue {top_other})")
        if q56 < q46:
            problems.append("DSCP 56 should have a higher queue than DSCP 46")
    if trust and not trusted:
        problems.append("No port trusts DSCP")
    if problems:
        return {"relevant": True, "status": "warning", "message": "; ".join(problems), "dscp": {"56": q56, "46": q46, "8": q8}, "trusted_ports": len(trusted)}
    return {"relevant": True, "status": "ok", "message": f"DSCP 56 → queue {q56}, DSCP 46 → queue {q46}; {len(trusted)} ports trust DSCP", "dscp": {"56": q56, "46": q46, "8": q8}, "trusted_ports": len(trusted)}

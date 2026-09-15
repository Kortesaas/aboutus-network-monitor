"""Access point state built from config, SNMP and topology evidence."""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from .checks import OFFLINE, ONLINE, UNKNOWN
from .devices import seconds_between


_WIRELESS_OR_VIRTUAL = re.compile(r"(wlan|wifi|ath|radio|ssid|vap|bridge|br-|loopback|lo$|vlan)", re.I)
_ETHERNET_HINT = re.compile(r"(eth|ethernet|lan|ge|gi|port)", re.I)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _same_text(a: Any, b: Any) -> bool:
    return re.sub(r"\s+", " ", _clean(a)).casefold() == re.sub(r"\s+", " ", _clean(b)).casefold()


def _warning(code: str, severity: str, title: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "title": title, "message": message}


def _sum_int(*values: Any) -> int | None:
    total = 0
    seen = False
    for value in values:
        if isinstance(value, int):
            total += value
            seen = True
    return total if seen else None


def _interface_summary(interface: dict[str, Any] | None) -> dict[str, Any] | None:
    if not interface:
        return None
    return {
        "if_index": interface.get("if_index"),
        "port": interface.get("port"),
        "name": interface.get("name"),
        "descr": interface.get("descr"),
        "alias": interface.get("alias"),
        "admin_status": interface.get("admin_status"),
        "oper_status": interface.get("oper_status"),
        "speed_mbps": interface.get("speed_mbps") or interface.get("nominal_speed_mbps"),
        "last_change_ticks": interface.get("last_change_ticks"),
        "in_bps": interface.get("in_bps"),
        "out_bps": interface.get("out_bps"),
        "in_octets": interface.get("in_octets"),
        "out_octets": interface.get("out_octets"),
        "errors": _sum_int(interface.get("in_errors"), interface.get("out_errors")),
        "discards": _sum_int(interface.get("in_discards"), interface.get("out_discards")),
        "errors_delta": interface.get("errors_delta") or 0,
        "discards_delta": interface.get("discards_delta") or 0,
        "sample_seconds": interface.get("sample_seconds"),
        "mac_address": interface.get("mac_address"),
    }


def _choose_uplink_interface(result: dict[str, Any]) -> dict[str, Any] | None:
    candidates = []
    for interface in (result.get("interfaces") or {}).values():
        name = f"{interface.get('name') or ''} {interface.get('descr') or ''} {interface.get('alias') or ''}"
        score = 0
        if interface.get("if_type") == 6:
            score += 40
        if _ETHERNET_HINT.search(name):
            score += 20
        if interface.get("oper_status") == "up":
            score += 15
        if interface.get("admin_status") == "up":
            score += 5
        if interface.get("speed_mbps"):
            score += min(10, int(interface["speed_mbps"]) // 100)
        if _WIRELESS_OR_VIRTUAL.search(name):
            score -= 50
        candidates.append((score, int(interface.get("if_index") or 0), interface))
    candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    if not candidates or candidates[0][0] <= 0:
        return None
    return candidates[0][2]


def _interfaces(result: dict[str, Any]) -> list[dict[str, Any]]:
    summaries = [_interface_summary(interface) for interface in (result.get("interfaces") or {}).values()]
    return sorted(
        [item for item in summaries if item],
        key=lambda item: (
            item.get("port") is None,
            int(item.get("port") or item.get("if_index") or 0),
            str(item.get("name") or ""),
        ),
    )


def _vlan_network(config: dict[str, Any], vlan_id: int | None) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    if vlan_id is None:
        return None
    for vlan in config.get("vlans") or []:
        if int(vlan.get("id") or -1) != int(vlan_id):
            continue
        subnet = vlan.get("subnet")
        if not subnet:
            return None
        try:
            return ipaddress.ip_network(str(subnet), strict=False)
        except ValueError:
            return None
    return None


def _find_expected_port(ap: dict[str, Any], switch_views: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    switch_id = ap.get("expected_switch_id")
    port_number = ap.get("expected_switch_port")
    if switch_id and port_number is not None:
        switch = next((item for item in switch_views if item.get("id") == switch_id), None)
        if switch:
            port = next((item for item in switch.get("ports") or [] if str(item.get("number")) == str(port_number)), None)
            return switch, port
    for switch in switch_views:
        for port in switch.get("ports") or []:
            if port.get("expected_neighbor") == ap.get("id"):
                return switch, port
    return None, None


def _find_topology_link(ap_id: str, switch_id: str | None, topology: dict[str, Any]) -> dict[str, Any] | None:
    for link in topology.get("links") or []:
        if ap_id not in {link.get("source"), link.get("target")}:
            continue
        if switch_id and switch_id not in {link.get("source"), link.get("target")}:
            continue
        return link
    return None


def _expected_trunk(config: dict[str, Any], port: dict[str, Any] | None) -> dict[str, Any]:
    policy = (config.get("policy") or {}).get("ap_trunk") or {}
    native = port.get("native") if port and port.get("native") is not None else policy.get("native_vlan", 99)
    tagged = list(port.get("tagged") or policy.get("tagged_vlans") or [10, 20, 30, 40]) if port else list(policy.get("tagged_vlans") or [10, 20, 30, 40])
    return {"native_vlan": native, "tagged_vlans": tagged}


def _uplink(ap: dict[str, Any], switch_views: list[dict[str, Any]], topology: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    switch, port = _find_expected_port(ap, switch_views)
    switch_id = switch.get("id") if switch else ap.get("expected_switch_id")
    port_number = port.get("number") if port else ap.get("expected_switch_port")
    link = _find_topology_link(ap["id"], switch_id, topology)
    observed = list((link or {}).get("observed") or [])
    confirmation = "confirmed" if observed else ("unconfirmed" if switch_id or port_number else "unknown")
    return {
        "expected": bool(switch_id or port_number),
        "confirmation": confirmation,
        "switch_id": switch_id,
        "switch_name": switch.get("name") if switch else switch_id,
        "port": port_number,
        "port_label": (port or {}).get("label") or ap.get("expected_switch_port_label"),
        "port_type": (port or {}).get("type"),
        "port_status": (port or {}).get("oper_status") or (link or {}).get("status"),
        "port_health": (port or {}).get("health") or (link or {}).get("health"),
        "speed_mbps": (port or {}).get("speed_mbps") or (link or {}).get("speed_mbps"),
        "in_bps": (port or {}).get("in_bps"),
        "out_bps": (port or {}).get("out_bps"),
        "errors": (port or {}).get("errors"),
        "discards": (port or {}).get("discards"),
        "issues": list((port or {}).get("issues") or []) + list((link or {}).get("issues") or []),
        "observed": observed,
        "topology_link_id": (link or {}).get("id"),
        "trunk": _expected_trunk(config, port),
    }


def _ap_state(item: dict[str, Any], result: dict[str, Any] | None) -> str:
    if not item.get("enabled", True):
        return "disabled"
    if item.get("status") == OFFLINE:
        return OFFLINE
    if result and result.get("status") == "ok":
        return ONLINE
    if item.get("status") == ONLINE and item.get("snmp_enabled"):
        return "stale"
    return item.get("status") or UNKNOWN


def build_access_point_views(
    *,
    config: dict[str, Any],
    infra: list[dict[str, Any]],
    snmp_devices: dict[str, dict[str, Any]],
    switch_views: list[dict[str, Any]],
    topology: dict[str, Any],
    now: str,
) -> list[dict[str, Any]]:
    """Return first-class AP state for /api/state and the UI."""
    aps = [item for item in infra if item.get("kind") == "ap"]
    expected_native = ((config.get("policy") or {}).get("ap_trunk") or {}).get("native_vlan", 99)
    views: list[dict[str, Any]] = []
    for ap in aps:
        result = snmp_devices.get(ap["id"]) or {}
        ok = result.get("status") == "ok"
        sys_info = result.get("sys") if ok else {}
        uplink_interface = _interface_summary(_choose_uplink_interface(result)) if ok else None
        uplink = _uplink(ap, switch_views, topology, config)
        state = _ap_state(ap, result)
        last_successful_poll = result.get("last_poll") if ok else None
        management_vlan = ap.get("management_vlan") if ap.get("management_vlan") is not None else ap.get("vlan")
        warnings: list[dict[str, str]] = []

        if state == OFFLINE:
            warnings.append(_warning("ap_unreachable", "warning", "AP unreachable", f"{ap.get('ip_address')} does not answer ping."))
        elif item_snmp := (ap.get("snmp") or {}):
            if ap.get("snmp_enabled") and result and result.get("status") != "ok":
                warnings.append(_warning("ap_stale", "warning", "SNMP stale", str(result.get("error") or "SNMP poll failed.")))
            elif ap.get("snmp_enabled") and item_snmp.get("status") in {"error", "pending"}:
                warnings.append(_warning("ap_stale", "warning", "SNMP stale", str(item_snmp.get("error") or "SNMP data is not current.")))

        expected_sys_name = _clean(ap.get("expected_sys_name") or ap.get("name"))
        if sys_info and sys_info.get("name") and expected_sys_name and not _same_text(sys_info.get("name"), expected_sys_name):
            warnings.append(_warning("ap_sys_name", "warning", "System name mismatch", f"SNMP sysName is '{sys_info.get('name')}', expected '{expected_sys_name}'."))

        expected_location = _clean(ap.get("expected_sys_location") or ap.get("location"))
        if sys_info and sys_info.get("location") and expected_location and not _same_text(sys_info.get("location"), expected_location):
            warnings.append(_warning("ap_location", "warning", "Location mismatch", f"SNMP sysLocation is '{sys_info.get('location')}', expected '{expected_location}'."))

        if management_vlan is not None and int(management_vlan) != int(expected_native):
            warnings.append(_warning("ap_management_vlan", "warning", "Management VLAN mismatch", f"AP management VLAN is {management_vlan}; AP trunks expect native VLAN {expected_native}."))
        if ap.get("vlan") is not None and management_vlan is not None and int(ap["vlan"]) != int(management_vlan):
            warnings.append(_warning("ap_management_vlan", "warning", "Management VLAN mismatch", f"Configured VLAN {ap.get('vlan')} does not match management VLAN {management_vlan}."))
        network = _vlan_network(config, int(management_vlan) if management_vlan is not None else None)
        if network and ap.get("ip_address"):
            try:
                if ipaddress.ip_address(str(ap["ip_address"])) not in network:
                    warnings.append(_warning("ap_management_ip", "warning", "Management IP mismatch", f"{ap['ip_address']} is not inside VLAN {management_vlan} subnet {network}."))
            except ValueError:
                warnings.append(_warning("ap_management_ip", "warning", "Management IP mismatch", f"{ap.get('ip_address')} is not a valid IP address."))

        if uplink.get("expected") and uplink.get("confirmation") == "unconfirmed":
            warnings.append(_warning("ap_uplink_unconfirmed", "info", "Uplink unconfirmed", "Expected AP trunk is configured, but LLDP/FDB has not confirmed the exact port."))
        if uplink.get("expected") and uplink.get("port_type") and uplink.get("port_type") != "ap_trunk":
            warnings.append(_warning("ap_uplink_profile", "warning", "Wrong uplink profile", f"Expected AP uplink is configured as {uplink.get('port_type')}, not ap_trunk."))
        if uplink.get("expected") and uplink.get("port_status") in {"down", "lowerLayerDown", "notPresent"}:
            warnings.append(_warning("ap_uplink_down", "warning", "Uplink down", f"Expected AP trunk port {uplink.get('switch_name') or '?'} {uplink.get('port') or '?'} is {uplink.get('port_status')}."))
        for issue in uplink.get("issues") or []:
            code = str(issue.get("code") or "")
            if code.startswith("ap_trunk"):
                warnings.append(_warning(code, issue.get("severity", "warning"), "AP trunk VLAN issue", issue.get("message", "AP trunk VLANs do not match the policy.")))

        views.append(
            {
                "id": ap["id"],
                "kind": "ap",
                "state": state,
                "status": state,
                "ping_status": ap.get("status"),
                "snmp_status": result.get("status") if result else (ap.get("snmp") or {}).get("status"),
                "name": ap.get("name"),
                "ip_address": ap.get("ip_address"),
                "vendor": ap.get("vendor"),
                "model": ap.get("model"),
                "location": ap.get("location"),
                "web_url": ap.get("web_url"),
                "enabled": ap.get("enabled", True),
                "planned": ap.get("planned", False),
                "optional": ap.get("optional", False),
                "management_vlan": management_vlan,
                "sys_name": (sys_info or {}).get("name"),
                "sys_descr": (sys_info or {}).get("descr"),
                "sys_location": (sys_info or {}).get("location"),
                "sys_contact": (sys_info or {}).get("contact"),
                "uptime": (sys_info or {}).get("uptime"),
                "uptime_seconds": (sys_info or {}).get("uptime_seconds"),
                "uplink": uplink,
                "ethernet": uplink_interface,
                "traffic": {
                    "in_bps": uplink_interface.get("in_bps") if uplink_interface else uplink.get("in_bps"),
                    "out_bps": uplink_interface.get("out_bps") if uplink_interface else uplink.get("out_bps"),
                },
                "errors": {
                    "errors": uplink_interface.get("errors") if uplink_interface else uplink.get("errors"),
                    "discards": uplink_interface.get("discards") if uplink_interface else uplink.get("discards"),
                    "errors_delta": uplink_interface.get("errors_delta") if uplink_interface else None,
                    "discards_delta": uplink_interface.get("discards_delta") if uplink_interface else None,
                },
                "client_count": None,
                "client_count_available": False,
                "client_count_note": "not available by SNMP",
                "interfaces": _interfaces(result)[:24] if ok else [],
                "last_successful_poll": last_successful_poll,
                "last_poll": result.get("last_poll") if result else (ap.get("snmp") or {}).get("last_poll"),
                "last_error": result.get("error") if result and result.get("status") != "ok" else None,
                "data_age_seconds": round(seconds_between(now, last_successful_poll), 1) if last_successful_poll else None,
                "poll_duration_ms": result.get("duration_ms"),
                "warnings": warnings,
            }
        )
    return views

"""Production problem detection for the ABOUTUS show network.

Every rule produces problems with a stable ``id`` so the monitor can track
first/last seen, raise and clear events, and let operators acknowledge them.
Severity: ``critical`` (show impacting), ``warning`` (should be fixed),
``info`` (attention / cannot be verified automatically).
"""

from __future__ import annotations

from typing import Any


FIXES = {
    "forbidden_vlan": "Remove VLAN 50 from the switch configuration. The current plan uses VLANs 10, 20, 30, 40, 99 (+90 WAN passthrough only).",
    "deprecated_name": "Rename to the current plan: VLAN 30 is LIGHT, VLAN 40 is VIDEO. LASER and LIGHTING no longer exist.",
    "trunk_missing_vlan": "Add the missing VLANs as tagged members of the trunk port (10, 20, 30, 40, 99).",
    "trunk_forbidden_vlan": "Remove VLAN 50 / VLAN 90 from the trunk. VLAN 90 is only allowed on dedicated WAN passthrough ports.",
    "ap_trunk_native": "Set VLAN 99 untagged (PVID 99) on the AP port and tag 10, 20, 30, 40.",
    "ap_trunk_pvid": "Set the PVID of the AP port to 99.",
    "trunk_untagged_client_vlan": "Trunks should carry client VLANs tagged only; move the untagged VLAN back to tagged.",
    "access_wrong_vlan": "Set the access port to the VLAN of its port group (1-8 CONTROL 10, 9-12 AUDIO 20, 13-16 LIGHT 30, 17-20 VIDEO 40).",
    "access_tagged_vlans": "Access ports should carry exactly one untagged VLAN; remove tagged VLANs.",
    "wan_pass_leak": "A WAN passthrough port must only carry VLAN 90.",
    "unconfigured_trunk": "Document the port in the monitor configuration (Settings → switches) or disable it on the switch.",
    "port_errors": "Check the cable/SFP and the far-end device; replace the patch cable if errors keep increasing.",
    "port_discards": "Discards on a trunk indicate congestion or a broadcast storm; check traffic on the far end.",
    "trunk_speed": "A trunk negotiated below 1 Gbit/s: check the cable (Cat5e/6) and the SFP module.",
    "access_speed": "Link negotiated at 10 Mbit/s: check the cable or the device NIC.",
    "uplink_down": "Check fibre/SFP and the far-end switch; the expected uplink is not up.",
    "expected_link_down": "The configured link is not up and no LLDP neighbour was seen; check cabling and power.",
    "lldp_mismatch": "The cable is patched to a different device than the plan expects. Re-patch or update the expected neighbour in Settings.",
    "infra_on_access_port": "Infrastructure is connected to an access port; configure it as a trunk (10, 20, 30, 40, 99) or move it.",
    "infra_offline": "Check power and MGMT VLAN 99 connectivity of the device.",
    "snmp_stale": "The device answers ping but not SNMP; verify the read-only community and SNMP access list.",
    "ap_sys_name": "Rename the AP sysName to the configured ABOUTUS AP name or update the inventory if the device was intentionally replaced.",
    "ap_location": "Set the AP sysLocation to the planned location or update the monitor configuration if the AP moved.",
    "ap_management_vlan": "The AP management interface should use native VLAN 99 on its AP trunk.",
    "ap_management_ip": "Assign the AP a management IP inside the VLAN 99 subnet.",
    "ap_uplink_unconfirmed": "Check LLDP/MAC learning on the AP trunk. The monitor will not guess an exact port without evidence.",
    "ap_uplink_down": "Check AP power, PoE/injector, cable and the configured AP trunk port.",
    "ap_uplink_profile": "Configure the expected AP uplink port as an AP trunk with native VLAN 99 and tagged client VLANs 10, 20, 30, 40.",
    "vlan_missing": "Create the missing VLAN on the switch and add it to all trunks.",
    "duplicate_ip": "Two devices use the same IP address; fix the static addressing.",
    "duplicate_mac": "One MAC answers on several IPs in the same VLAN; usually a virtual interface or a misconfigured device.",
    "rstp_root": "Make the FOH core switch the RSTP root (lowest bridge priority, e.g. 4096) and give stage switches a higher priority.",
    "rstp_unknown": "RSTP root cannot be read via SNMP on this model; verify on the switch CLI: 'show spanning-tree'.",
    "dante_qos": "Configure Dante QoS: DSCP 56 (CS7) in the highest strict queue, DSCP 46 (EF) in the next queue, trust DSCP on all ports.",
    "eee": "Disable Energy Efficient Ethernet / Green Ethernet on all AUDIO ports and trunks carrying Dante.",
    "gateway_down": "The VLAN gateway on the LANCOM does not answer; check the router VLAN interface.",
    "internet_down": "Internet probes fail; check the WAN uplink of the LANCOM.",
    "unknown_device": "Identify the device and give it a name in the device list (or mark it ignored).",
    "unlocated_device": "The device answers but its switch port could not be proven; check that it is plugged into an access port of a monitored switch.",
    "expected_device_offline": "An inventory device is offline; check its power and cable.",
    "tool": "Install the missing tool on the Raspberry Pi (apt install snmp fping nmap).",
    "stp_disabled": "Spanning tree appears disabled on this switch; enable RSTP to protect against loops.",
}


def _problem(problem_id: str, severity: str, category: str, title: str, detail: str, affected: dict[str, Any], fix_key: str | None = None) -> dict[str, Any]:
    return {
        "id": problem_id,
        "severity": severity,
        "category": category,
        "title": title,
        "detail": detail,
        "affected": affected,
        "fix": FIXES.get(fix_key or category, ""),
    }


def detect_problems(
    *,
    config: dict[str, Any],
    infra: list[dict[str, Any]],
    switch_views: list[dict[str, Any]],
    topology: dict[str, Any],
    devices: list[dict[str, Any]],
    snmp: dict[str, Any],
    vlan_status: list[dict[str, Any]],
    internet: dict[str, Any],
    discovery: dict[str, Any],
    ip_mac: dict[str, dict[str, Any]],
    access_points: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    policy = config["policy"]
    problems: list[dict[str, Any]] = []
    forbidden = set(policy.get("forbidden_vlans") or [])
    deprecated = [name.upper() for name in policy.get("deprecated_names") or []]
    required_vlans = set(policy.get("trunk_vlans") or [])
    switch_config = {switch["id"]: switch for switch in config["switches"]}
    infra_by_id = {item["id"]: item for item in infra}

    # --- infrastructure reachability -----------------------------------------------
    for item in infra:
        if not item.get("enabled", True):
            continue
        affected = {"kind": item.get("kind"), "id": item["id"], "name": item["name"], "ip_address": item.get("ip_address")}
        if item.get("status") == "offline":
            if item.get("planned"):
                severity, title = "info", f"{item['name']} (planned) is not reachable"
            elif item.get("optional"):
                severity, title = "warning", f"{item['name']} (optional) is offline"
            else:
                severity, title = "critical", f"{item['name']} is offline"
            problems.append(_problem(f"infra_offline:{item['id']}", severity, "infra_offline", title, f"{item.get('ip_address')} does not answer ping.", affected))
        elif item.get("status") == "online" and item.get("snmp_enabled") and item.get("kind") in {"switch", "router", "ap"}:
            result = (snmp.get("devices") or {}).get(item["id"])
            if snmp.get("enabled") and result and result.get("status") != "ok":
                problems.append(_problem(f"snmp_stale:{item['id']}", "warning", "snmp_stale", f"{item['name']}: SNMP not responding", str(result.get("error") or "SNMP poll failed."), affected))

    if snmp.get("status") == "missing_tools":
        problems.append(_problem("tool:snmp", "warning", "tool", "SNMP tools missing on the Pi", "; ".join(snmp.get("errors") or []), {"kind": "monitor", "id": "aboutus-pi", "name": "Monitor"}))
    if discovery.get("status") == "error":
        problems.append(_problem("tool:discovery", "info", "tool", "Discovery is not working", "; ".join(discovery.get("errors") or []), {"kind": "monitor", "id": "aboutus-pi", "name": "Monitor"}))

    # --- VLAN gateways / internet ------------------------------------------------------------
    for vlan in vlan_status:
        if vlan.get("gateway") and vlan.get("gateway_status") == "offline":
            problems.append(_problem(f"gateway_down:{vlan['id']}", "critical", "gateway_down", f"VLAN {vlan['id']} {vlan['name']} gateway {vlan['gateway']} unreachable", "The router interface for this VLAN does not answer ping from the monitor.", {"kind": "vlan", "id": str(vlan["id"]), "name": vlan["name"]}))
    if internet.get("probes") and internet.get("status") == "offline":
        problems.append(_problem("internet_down", "warning", "internet_down", "Internet is unreachable", ", ".join(f"{probe['name']}: {probe['status']}" for probe in internet.get("probes") or []), {"kind": "internet", "id": "internet", "name": "Internet"}))

    # --- switch level checks --------------------------------------------------------------------
    for view in switch_views:
        switch_affected = {"kind": "switch", "id": view["id"], "name": view["name"], "ip_address": view.get("ip_address")}
        snmp_ok = view.get("snmp_status") == "ok"
        vlan_ids = {int(v["id"]) for v in view.get("vlans") or []}
        if snmp_ok and vlan_ids:
            for vlan_id in sorted(vlan_ids & forbidden):
                name = next((v.get("name") for v in view["vlans"] if int(v["id"]) == vlan_id), "")
                problems.append(_problem(f"forbidden_vlan:{view['id']}:{vlan_id}", "critical", "forbidden_vlan", f"VLAN {vlan_id} exists on {view['name']}", f"VLAN {vlan_id} ({name or 'unnamed'}) is deprecated and must not exist anywhere in the production network.", switch_affected))
            for vlan in view.get("vlans") or []:
                name = str(vlan.get("name") or "").upper()
                if any(token in name for token in deprecated):
                    problems.append(_problem(f"deprecated_name:{view['id']}:vlan:{vlan['id']}", "warning", "deprecated_name", f"VLAN {vlan['id']} on {view['name']} is still named '{vlan.get('name')}'", "Old LASER/LIGHTING naming is still present in the switch configuration.", switch_affected))
            for port in view["ports"]:
                alias = str(port.get("alias") or "").upper()
                if any(token in alias for token in deprecated):
                    problems.append(_problem(f"deprecated_name:{view['id']}:port:{port['number']}", "warning", "deprecated_name", f"{view['name']} port {port['number']} description is '{port.get('alias')}'", "Old LASER/LIGHTING naming is still present in a port description.", {**switch_affected, "port": port["number"]}))
            missing = required_vlans - vlan_ids
            if missing and not view.get("optional"):
                problems.append(_problem(f"vlan_missing:{view['id']}", "warning", "vlan_missing", f"{view['name']} is missing VLAN {', '.join(str(v) for v in sorted(missing))}", "Required production VLANs are not defined on this switch.", switch_affected))
            elif missing:
                problems.append(_problem(f"vlan_missing:{view['id']}", "warning", "vlan_missing", f"{view['name']} is missing VLAN {', '.join(str(v) for v in sorted(missing))}", "Required production VLANs are not defined on this optional switch.", switch_affected))

        # per port issues
        for port in view["ports"]:
            for issue in port.get("issues") or []:
                label = f" ({port['label']})" if port.get("label") else ""
                problems.append(
                    _problem(
                        f"port:{view['id']}:{port['number']}:{issue['code']}",
                        issue["severity"],
                        issue["code"],
                        f"{view['name']} port {port['number']}{label}: {issue['message']}",
                        f"Expected profile {port.get('profile')} ({port.get('type')}). Actual: untagged {port.get('actual', {}).get('untagged') if port.get('actual') else 'n/a'}, tagged {port.get('actual', {}).get('tagged') if port.get('actual') else 'n/a'}, PVID {port.get('actual', {}).get('pvid') if port.get('actual') else 'n/a'}.",
                        {**switch_affected, "port": port["number"]},
                    )
                )

        # spanning tree
        stp = view.get("stp") or {}
        core_id = policy.get("rstp_root_switch")
        if snmp_ok:
            if stp.get("supported"):
                if not stp.get("root_mac") or stp.get("root_mac") == "00:00:00:00:00:00":
                    problems.append(_problem(f"stp_disabled:{view['id']}", "info", "stp_disabled", f"{view['name']}: spanning tree not active", "The switch reports no designated root bridge.", switch_affected))
                elif view["id"] == core_id and not stp.get("is_root"):
                    problems.append(_problem(f"rstp_root:{view['id']}", "critical", "rstp_root", f"{view['name']} is not the RSTP root", f"Root bridge is {stp.get('root_mac')} (priority {stp.get('root_priority')}).", switch_affected))
                elif view["id"] != core_id and stp.get("is_root") and core_id in switch_config and infra_by_id.get(core_id, {}).get("status") == "online":
                    problems.append(_problem(f"rstp_root:{view['id']}", "warning", "rstp_root", f"{view['name']} believes it is the RSTP root", "The FOH core switch should be the root bridge. This switch may not be connected to the core by a trunk, or priorities are wrong.", switch_affected))
            elif view["id"] == core_id or switch_config.get(view["id"], {}).get("rstp_root_expected"):
                problems.append(_problem(f"rstp_unknown:{view['id']}", "info", "rstp_unknown", f"{view['name']}: RSTP root not readable via SNMP", "The standard BRIDGE-MIB spanning tree objects are not exposed by this switch.", switch_affected))

        # Dante QoS / EEE
        dante = view.get("dante") or {}
        raw_switch = next((s for s in config["raw"].get("switches") or [] if str(s.get("id")) == view["id"]), {})
        if dante.get("relevant") and snmp_ok:
            if dante.get("status") == "warning":
                problems.append(_problem(f"dante_qos:{view['id']}", "warning", "dante_qos", f"{view['name']}: Dante QoS not as recommended", dante.get("message", ""), switch_affected))
            elif dante.get("status") == "unknown" and not raw_switch.get("dante_qos_verified"):
                problems.append(_problem(f"dante_qos:{view['id']}", "info", "dante_qos", f"{view['name']}: Dante QoS cannot be verified", dante.get("message", "") + " Set dante_qos_verified: true in the switch settings once checked.", switch_affected))
            if not raw_switch.get("eee_disabled_verified"):
                problems.append(_problem(f"eee:{view['id']}", "info", "eee", f"{view['name']}: EEE / Green Ethernet not verifiable", "Energy Efficient Ethernet cannot be read via SNMP on this switch. Dante requires EEE off on audio ports and trunks. Set eee_disabled_verified: true in the switch settings once checked.", switch_affected))

    # --- topology link issues -----------------------------------------------------------------
    seen_link_issues: set[str] = set()
    for link in topology.get("links") or []:
        for issue in link.get("issues") or []:
            key = f"link:{link['id']}:{issue['code']}"
            if key in seen_link_issues:
                continue
            seen_link_issues.add(key)
            source = infra_by_id.get(link["source"], {})
            target = infra_by_id.get(link["target"], {})
            problems.append(_problem(key, issue["severity"], issue["code"], issue["message"], f"Link {source.get('name', link['source'])} port {link.get('source_port') or '?'} ↔ {target.get('name', link['target'])} port {link.get('target_port') or '?'}. Evidence: {', '.join(link.get('observed') or []) or 'none'}.", {"kind": "link", "id": link["id"], "name": f"{source.get('name', link['source'])} ↔ {target.get('name', link['target'])}", "switch_id": link["source"], "port": link.get("source_port")}))

    # --- access point plan checks --------------------------------------------------------------
    for ap in access_points or []:
        affected = {"kind": "ap", "id": ap["id"], "name": ap["name"], "ip_address": ap.get("ip_address"), "switch_id": (ap.get("uplink") or {}).get("switch_id"), "port": (ap.get("uplink") or {}).get("port")}
        for warning in ap.get("warnings") or []:
            code = warning.get("code") or "ap_warning"
            if code in {"ap_unreachable", "ap_stale"}:
                continue
            problems.append(
                _problem(
                    f"ap:{ap['id']}:{code}",
                    warning.get("severity", "warning"),
                    code,
                    f"{ap['name']}: {warning.get('title') or code.replace('_', ' ')}",
                    warning.get("message", ""),
                    affected,
                    code,
                )
            )

    # --- duplicates -----------------------------------------------------------------------------
    infra_ips: dict[str, list[str]] = {}
    for item in infra:
        if item.get("ip_address") and item.get("enabled", True):
            infra_ips.setdefault(item["ip_address"], []).append(item["name"])
    for ip_address, names in infra_ips.items():
        if len(names) > 1:
            problems.append(_problem(f"duplicate_ip:infra:{ip_address}", "critical", "duplicate_ip", f"Management IP {ip_address} is configured twice", ", ".join(names), {"kind": "config", "id": ip_address, "name": ip_address}))
    by_ip: dict[str, list[dict[str, Any]]] = {}
    for device in devices:
        if device.get("ip_address"):
            by_ip.setdefault(device["ip_address"], []).append(device)
    for ip_address, group in by_ip.items():
        macs = {device.get("mac_address") for device in group if device.get("mac_address")}
        if len(group) > 1 and len(macs) > 1 and any(device["state"] in {"active_located", "active_unlocated"} for device in group):
            problems.append(_problem(f"duplicate_ip:{ip_address}", "critical", "duplicate_ip", f"IP {ip_address} is used by {len(macs)} MAC addresses", ", ".join(f"{d['display_name']} ({d.get('mac_address')})" for d in group), {"kind": "device", "id": group[0]["key"], "name": ip_address}))
    by_mac_vlan: dict[tuple[str, int | None], set[str]] = {}
    vlan_lookup = {vlan["id"]: vlan for vlan in config["vlans"]}
    from .devices import vlan_for_ip, vlan_networks  # local import to avoid a cycle at module load

    networks = vlan_networks(config["vlans"])
    for ip_address, observation in ip_mac.items():
        vlan = vlan_for_ip(ip_address, networks)
        by_mac_vlan.setdefault((observation["mac_address"], vlan["id"] if vlan else None), set()).add(ip_address)
    for (mac, vlan_id), ips in by_mac_vlan.items():
        if len(ips) > 1 and vlan_id is not None and mac not in {item.get("mac_address") for item in infra}:
            # the router owns every gateway address, ignore gateways
            if ips <= {vlan_lookup[vlan_id].get("gateway")}:
                continue
            problems.append(_problem(f"duplicate_mac:{mac}:{vlan_id}", "info", "duplicate_mac", f"MAC {mac.upper()} answers on {len(ips)} IPs in VLAN {vlan_id}", ", ".join(sorted(ips)), {"kind": "device", "id": f"mac:{mac}", "name": mac.upper()}))

    # --- device level -----------------------------------------------------------------------------
    for device in devices:
        if device.get("is_infrastructure") or device.get("ignored"):
            continue
        affected = {"kind": "device", "id": device["key"], "name": device["display_name"], "ip_address": device.get("ip_address"), "switch_id": (device.get("location") or {}).get("switch_id"), "port": (device.get("location") or {}).get("port")}
        known = bool(device.get("metadata", {}).get("saved") or device.get("inventory"))
        if device["state"] == "active_unlocated":
            if not known:
                problems.append(_problem(f"unknown_device:{device['key']}", "info", "unknown_device", f"Unknown device online: {device['display_name']}", f"{device.get('ip_address') or device.get('mac_address')} in VLAN {device.get('vlan_id') or '?'} is online but not identified and not located on a switch port." + (f" {device['path']['message']}." if device.get("path") else ""), affected))
            else:
                problems.append(_problem(f"unlocated_device:{device['key']}", "info", "unlocated_device", f"{device['display_name']} is online but not located", f"{device.get('ip_address') or ''} answers, but no switch access port could be proven." + (f" {device['path']['message']}." if device.get("path") else ""), affected))
        elif device["state"] == "active_located" and not known:
            problems.append(_problem(f"unknown_device:{device['key']}", "info", "unknown_device", f"Unknown device: {device['display_name']}", f"{device.get('ip_address') or device.get('mac_address')} on {device['location']['switch_name']} port {device['location']['port']} (VLAN {device.get('vlan_id')}) has no name yet.", affected))
        if device.get("expected") and device.get("inventory") and device["state"] in {"offline", "stale"}:
            problems.append(_problem(f"expected_device_offline:{device['key']}", "warning", "expected_device_offline", f"Expected device {device['display_name']} is {device['state']}", f"Last seen {device.get('last_seen') or 'never'}.", affected))

    order = {"critical": 0, "warning": 1, "info": 2}
    problems.sort(key=lambda item: (order.get(item["severity"], 3), item["category"], item["title"]))
    return problems

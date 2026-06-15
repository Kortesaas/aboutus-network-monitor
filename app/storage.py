from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .checks import ONLINE, UNKNOWN
from .config import PROJECT_ROOT


DB_ENV_VAR = "ABOUTUS_MONITOR_DB"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "monitor.sqlite3"
UNKNOWN_LABEL = "Unknown"


def get_db_path() -> Path:
    configured_path = os.getenv(DB_ENV_VAR)
    if configured_path:
        return Path(configured_path).expanduser()
    return DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=3000")
    return connection


def _init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS device_state (
            device_key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            ip_address TEXT,
            mac_address TEXT,
            hostname TEXT,
            vendor TEXT,
            vlan_name TEXT,
            vlan_id TEXT,
            expected INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            previous_status TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT,
            last_present TEXT,
            last_checked TEXT NOT NULL,
            last_status_change TEXT NOT NULL,
            offline_since TEXT,
            discovery_sources TEXT
        );

        CREATE TABLE IF NOT EXISTS device_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time TEXT NOT NULL,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            device_key TEXT,
            display_name TEXT,
            ip_address TEXT,
            vlan_name TEXT,
            from_status TEXT,
            to_status TEXT,
            message TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_device_events_time
            ON device_events(event_time DESC);

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            summary_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_time
            ON snapshots(generated_at DESC);
        """
    )
    _ensure_column(connection, "device_state", "mac_address", "TEXT")
    _ensure_column(connection, "device_state", "hostname", "TEXT")
    _ensure_column(connection, "device_state", "vendor", "TEXT")
    _ensure_column(connection, "device_state", "last_present", "TEXT")
    _ensure_column(connection, "device_state", "discovery_sources", "TEXT")


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def _known(value: Any) -> bool:
    if value is None:
        return False
    prepared = str(value).strip()
    return bool(prepared) and prepared.lower() not in {"unknown", "none", "null", "n/a"}


def _device_ip(device: dict[str, Any]) -> str:
    return str(device.get("ip_address") or device.get("ip") or "")


def _normal_mac(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", ":")


def _device_id(device: dict[str, Any]) -> str:
    return str(device.get("id") or "").strip()


def _device_key(device: dict[str, Any]) -> str:
    if _known(device.get("device_key")):
        return str(device["device_key"])

    mac_address = _normal_mac(device.get("mac_address") or device.get("mac"))
    if _known(mac_address):
        return f"mac:{mac_address}"

    device_id = _device_id(device)
    if _known(device_id) and not device_id.startswith("discovered-"):
        return f"id:{device_id}"

    ip_address = _device_ip(device)
    if _known(ip_address):
        return f"ip:{ip_address}"

    for key in ("display_name", "name"):
        value = device.get(key)
        if _known(value):
            return f"name:{str(value).strip().lower()}"
    return "unknown-device"


def _candidate_device_keys(device: dict[str, Any]) -> list[str]:
    values = [
        _device_key(device),
        _device_ip(device),
        _device_id(device),
        _normal_mac(device.get("mac_address") or device.get("mac")),
        str(device.get("hostname") or ""),
    ]
    result: list[str] = []
    for value in values:
        prepared = value.strip()
        if _known(prepared) and prepared not in result:
            result.append(prepared)
    return result


def _device_name(device: dict[str, Any]) -> str:
    return str(device.get("display_name") or device.get("name") or device.get("ip_address") or UNKNOWN_LABEL)


def _vlan_label(vlan_name: Any, vlan_id: Any) -> str:
    if _known(vlan_name) and _known(vlan_id):
        return f"{vlan_name} / VLAN {vlan_id}"
    if _known(vlan_name):
        return str(vlan_name)
    if _known(vlan_id):
        return f"VLAN {vlan_id}"
    return UNKNOWN_LABEL


def _device_vlan_label(device: dict[str, Any]) -> str:
    return _vlan_label(device.get("vlan_name") or device.get("vlan"), device.get("vlan_id"))


def _row_vlan_label(row: sqlite3.Row) -> str:
    return _vlan_label(row["vlan_name"], row["vlan_id"])


def _ip_context(ip_address: Any) -> str:
    return f" at {ip_address}" if _known(ip_address) else ""


def _source_signature(device: dict[str, Any]) -> str:
    return ", ".join(str(source) for source in device.get("discovery_sources") or [])


def _row_to_device(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "device_key": row["device_key"],
        "display_name": row["display_name"],
        "name": row["display_name"],
        "ip_address": row["ip_address"],
        "mac_address": row["mac_address"],
        "hostname": row["hostname"],
        "vendor": row["vendor"],
        "vlan_name": row["vlan_name"],
        "vlan_id": row["vlan_id"],
        "expected": bool(row["expected"]),
        "status": row["status"],
        "discovery_sources": (row["discovery_sources"] or "").split(", ") if row["discovery_sources"] else [],
    }


def _find_existing_state(connection: sqlite3.Connection, device: dict[str, Any]) -> sqlite3.Row | None:
    preferred = _device_key(device)
    preferred_row = connection.execute(
        "SELECT * FROM device_state WHERE device_key = ?",
        (preferred,),
    ).fetchone()

    candidates = _candidate_device_keys(device)
    if not candidates:
        return preferred_row

    if preferred_row:
        duplicate_keys = [key for key in candidates if key != preferred]
        if duplicate_keys:
            placeholders = ", ".join("?" for _ in duplicate_keys)
            connection.execute(
                f"DELETE FROM device_state WHERE device_key IN ({placeholders})",
                duplicate_keys,
            )
        return preferred_row

    placeholders = ", ".join("?" for _ in candidates)
    row = connection.execute(
        f"SELECT * FROM device_state WHERE device_key IN ({placeholders}) ORDER BY last_checked DESC LIMIT 1",
        candidates,
    ).fetchone()

    if row and row["device_key"] != preferred:
        try:
            connection.execute(
                "UPDATE device_state SET device_key = ? WHERE device_key = ?",
                (preferred, row["device_key"]),
            )
            connection.execute(
                "UPDATE device_events SET device_key = ? WHERE device_key = ?",
                (preferred, row["device_key"]),
            )
            row = connection.execute(
                "SELECT * FROM device_state WHERE device_key = ?",
                (preferred,),
            ).fetchone()
        except sqlite3.IntegrityError:
            row = connection.execute(
                "SELECT * FROM device_state WHERE device_key = ?",
                (preferred,),
            ).fetchone() or row
    return row


def _has_stable_identity(device: dict[str, Any]) -> bool:
    mac_address = _normal_mac(device.get("mac_address") or device.get("mac"))
    if _known(mac_address):
        return True

    device_id = _device_id(device)
    if _known(device_id) and not device_id.startswith("discovered-"):
        return True

    if _known(device.get("hostname")):
        return True

    return False


def _event_for_status_change(
    *,
    device: dict[str, Any],
    from_status: str,
    to_status: str,
) -> tuple[str, str, str]:
    name = _device_name(device)
    expected = bool(device.get("expected"))
    vlan = _device_vlan_label(device)
    ip_address = _device_ip(device)
    if to_status == ONLINE:
        return "recovered", "info", f"{name} is back online on {vlan}{_ip_context(ip_address)}."
    if to_status == "offline" and expected:
        return "expected_offline", "warning", f"Expected device {name} went offline on {vlan}{_ip_context(ip_address)}."
    if to_status == "offline":
        return "offline", "info", f"{name} went offline on {vlan}{_ip_context(ip_address)}."
    if to_status == UNKNOWN:
        return "unknown", "warning" if expected else "info", f"{name} status is unknown on {vlan}{_ip_context(ip_address)}."
    return "status_change", "info", f"{name} changed from {from_status} to {to_status} on {vlan}{_ip_context(ip_address)}."


def _emit_change_events(
    connection: sqlite3.Connection,
    *,
    generated_at: str,
    device: dict[str, Any],
    existing: sqlite3.Row | None,
) -> None:
    if not existing:
        return

    name = _device_name(device)
    old_vlan = _row_vlan_label(existing)
    new_vlan = _device_vlan_label(device)
    old_ip = existing["ip_address"]
    new_ip = _device_ip(device)

    if _has_stable_identity(device) and old_vlan != new_vlan and _known(old_vlan) and _known(new_vlan):
        _insert_event(
            connection,
            event_time=generated_at,
            event_type="vlan_changed",
            severity="info",
            device=device,
            from_status=existing["status"],
            to_status=device.get("status"),
            message=f"{name} moved from {old_vlan} to {new_vlan}{_ip_context(new_ip)}.",
        )

    if _has_stable_identity(device) and _known(old_ip) and _known(new_ip) and str(old_ip) != str(new_ip):
        _insert_event(
            connection,
            event_time=generated_at,
            event_type="ip_changed",
            severity="info",
            device=device,
            from_status=existing["status"],
            to_status=device.get("status"),
            message=f"{name} changed IP from {old_ip} to {new_ip} on {new_vlan}.",
        )


def _insert_event(
    connection: sqlite3.Connection,
    *,
    event_time: str,
    event_type: str,
    severity: str,
    device: dict[str, Any],
    from_status: str | None,
    to_status: str | None,
    message: str,
) -> None:
    connection.execute(
        """
        INSERT INTO device_events (
            event_time, event_type, severity, device_key, display_name, ip_address,
            vlan_name, from_status, to_status, message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_time,
            event_type,
            severity,
            _device_key(device),
            _device_name(device),
            str(device.get("ip_address") or ""),
            str(device.get("vlan_name") or device.get("vlan") or ""),
            from_status,
            to_status,
            message,
        ),
    )


def _snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    devices = snapshot.get("devices") or []
    warnings = snapshot.get("warnings") or []
    vlans = snapshot.get("vlans") or []
    return {
        "infrastructure_online": sum(1 for item in snapshot.get("infrastructure") or [] if item.get("status") == ONLINE),
        "infrastructure_total": len(snapshot.get("infrastructure") or []),
        "internet_status": (snapshot.get("internet") or {}).get("status", UNKNOWN),
        "vlan_gateways_online": sum(1 for vlan in vlans if vlan.get("gateway_status") == ONLINE),
        "vlan_gateways_total": len(vlans),
        "devices_total": len(devices),
        "devices_online": sum(1 for device in devices if device.get("status") == ONLINE),
        "devices_offline": sum(1 for device in devices if device.get("status") == "offline"),
        "devices_unknown": sum(1 for device in devices if device.get("status") == UNKNOWN),
        "warnings": len(warnings),
    }


def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    generated_at = str(snapshot.get("generated_at") or "")
    if not generated_at:
        return snapshot

    with _connect() as connection:
        _init_db(connection)
        present_keys: set[str] = set()
        for device in snapshot.get("devices") or []:
            key = _device_key(device)
            present_keys.add(key)
            status = str(device.get("status") or UNKNOWN)
            existing = _find_existing_state(connection, device)

            first_seen = existing["first_seen"] if existing else generated_at
            previous_status = existing["status"] if existing else None
            status_changed = bool(existing and previous_status != status)
            last_status_change = generated_at if status_changed or not existing else existing["last_status_change"]

            if status == ONLINE:
                last_seen = str(device.get("last_seen") or generated_at)
                offline_since = None
            else:
                configured_last_seen = device.get("last_seen")
                last_seen = (
                    str(configured_last_seen)
                    if _known(configured_last_seen)
                    else (existing["last_seen"] if existing and existing["last_seen"] else None)
                )
                if status == "offline":
                    offline_since = generated_at if not existing or previous_status == ONLINE else existing["offline_since"]
                else:
                    offline_since = existing["offline_since"] if existing else None

            if not existing:
                severity = "info" if status == ONLINE else ("warning" if device.get("expected") else "info")
                vlan = _device_vlan_label(device)
                ip_address = _device_ip(device)
                _insert_event(
                    connection,
                    event_time=generated_at,
                    event_type="joined",
                    severity=severity,
                    device=device,
                    from_status=None,
                    to_status=status,
                    message=f"{_device_name(device)} joined {vlan}{_ip_context(ip_address)} as {status}.",
                )
            else:
                _emit_change_events(
                    connection,
                    generated_at=generated_at,
                    device=device,
                    existing=existing,
                )
                if status_changed:
                    event_type, severity, message = _event_for_status_change(
                        device=device,
                        from_status=str(previous_status),
                        to_status=status,
                    )
                    _insert_event(
                        connection,
                        event_time=generated_at,
                        event_type=event_type,
                        severity=severity,
                        device=device,
                        from_status=str(previous_status),
                        to_status=status,
                        message=message,
                    )

            connection.execute(
                """
                INSERT INTO device_state (
                    device_key, display_name, ip_address, mac_address, hostname, vendor,
                    vlan_name, vlan_id, expected,
                    status, previous_status, first_seen, last_seen, last_checked,
                    last_present, last_status_change, offline_since, discovery_sources
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    ip_address = excluded.ip_address,
                    mac_address = excluded.mac_address,
                    hostname = excluded.hostname,
                    vendor = excluded.vendor,
                    vlan_name = excluded.vlan_name,
                    vlan_id = excluded.vlan_id,
                    expected = excluded.expected,
                    previous_status = excluded.previous_status,
                    status = excluded.status,
                    last_seen = excluded.last_seen,
                    last_present = excluded.last_present,
                    last_checked = excluded.last_checked,
                    last_status_change = excluded.last_status_change,
                    offline_since = excluded.offline_since,
                    discovery_sources = excluded.discovery_sources
                """,
                (
                    key,
                    _device_name(device),
                    _device_ip(device),
                    str(device.get("mac_address") or device.get("mac") or ""),
                    str(device.get("hostname") or ""),
                    str(device.get("vendor") or ""),
                    str(device.get("vlan_name") or device.get("vlan") or ""),
                    str(device.get("vlan_id") or ""),
                    1 if device.get("expected") else 0,
                    status,
                    previous_status,
                    first_seen,
                    last_seen,
                    generated_at,
                    generated_at if status == ONLINE else (existing["last_present"] if existing else None),
                    last_status_change,
                    offline_since,
                    _source_signature(device),
                ),
            )

            device["history"] = {
                "first_seen": first_seen,
                "last_seen": last_seen or UNKNOWN_LABEL,
                "last_checked": generated_at,
                "last_status_change": last_status_change,
                "previous_status": previous_status or UNKNOWN_LABEL,
                "offline_since": offline_since or UNKNOWN_LABEL,
            }
            if not _known(device.get("last_seen")) and last_seen:
                device["last_seen"] = last_seen

        discovery_status = (snapshot.get("discovery") or {}).get("status")
        if discovery_status == "ok":
            if present_keys:
                placeholders = ", ".join("?" for _ in present_keys)
                missing_rows = connection.execute(
                    f"""
                    SELECT * FROM device_state
                    WHERE device_key NOT IN ({placeholders})
                      AND status != 'offline'
                      AND expected = 0
                    """,
                    tuple(present_keys),
                ).fetchall()
            else:
                missing_rows = connection.execute(
                    """
                    SELECT * FROM device_state
                    WHERE status != 'offline'
                      AND expected = 0
                    """
                ).fetchall()

            for row in missing_rows:
                old_device = _row_to_device(row)
                _insert_event(
                    connection,
                    event_time=generated_at,
                    event_type="left",
                    severity="info",
                    device=old_device,
                    from_status=row["status"],
                    to_status="offline",
                    message=f"{row['display_name']} left {_row_vlan_label(row)}{_ip_context(row['ip_address'])}.",
                )
                connection.execute(
                    """
                    UPDATE device_state
                    SET previous_status = status,
                        status = 'offline',
                        last_checked = ?,
                        last_status_change = ?,
                        offline_since = ?
                    WHERE device_key = ?
                    """,
                    (generated_at, generated_at, generated_at, row["device_key"]),
                )

        connection.execute(
            "INSERT INTO snapshots (generated_at, summary_json) VALUES (?, ?)",
            (generated_at, json.dumps(_snapshot_summary(snapshot), sort_keys=True)),
        )
        connection.execute(
            "DELETE FROM device_events WHERE id NOT IN (SELECT id FROM device_events ORDER BY event_time DESC, id DESC LIMIT 1000)"
        )
        connection.execute(
            "DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY generated_at DESC, id DESC LIMIT 500)"
        )

    snapshot["history"] = history_payload(limit=40)
    return snapshot


def history_payload(limit: int = 40) -> dict[str, Any]:
    with _connect() as connection:
        _init_db(connection)
        events = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, event_time, event_type, severity, device_key, display_name,
                       ip_address, vlan_name, from_status, to_status, message
                FROM device_events
                ORDER BY event_time DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
        latest_snapshot = connection.execute(
            "SELECT generated_at, summary_json FROM snapshots ORDER BY generated_at DESC, id DESC LIMIT 1"
        ).fetchone()
        device_totals = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'online' THEN 1 ELSE 0 END) AS online,
                SUM(CASE WHEN status = 'offline' THEN 1 ELSE 0 END) AS offline,
                SUM(CASE WHEN status = 'unknown' THEN 1 ELSE 0 END) AS unknown
            FROM device_state
            """
        ).fetchone()

    return {
        "database_path": str(get_db_path()),
        "latest_snapshot": {
            "generated_at": latest_snapshot["generated_at"] if latest_snapshot else None,
            "summary": json.loads(latest_snapshot["summary_json"]) if latest_snapshot else {},
        },
        "device_totals": dict(device_totals) if device_totals else {},
        "events": events,
    }

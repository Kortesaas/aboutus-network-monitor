"""SQLite persistence: known-device metadata, device history, events, problems.

The database lives in ``data/monitor.sqlite3`` (override with
``ABOUTUS_MONITOR_DB``). Everything the backend needs to survive a restart is
stored here; browsers keep nothing but cosmetic preferences.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .neighbors import normalize_mac


DB_ENV_VAR = "ABOUTUS_MONITOR_DB"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "monitor.sqlite3"
_LOCK = threading.RLock()
_INITIALIZED = False

METADATA_FIELDS = ("display_name", "owner", "device_type", "criticality", "asset_tag", "notes")


def get_db_path() -> Path:
    configured_path = os.getenv(DB_ENV_VAR)
    if configured_path:
        return Path(configured_path).expanduser()
    return DEFAULT_DB_PATH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    global _INITIALIZED
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=3000")
    if not _INITIALIZED:
        _init_db(connection)
        _INITIALIZED = True
    return connection


def _init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS device_metadata (
            identity_key TEXT PRIMARY KEY,
            mac_address TEXT NOT NULL,
            mac_addresses TEXT,
            display_name TEXT,
            owner TEXT,
            device_type TEXT,
            criticality TEXT,
            asset_tag TEXT,
            notes TEXT,
            favorite INTEGER NOT NULL DEFAULT 0,
            ignored INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS device_records (
            key TEXT PRIMARY KEY,
            first_seen TEXT NOT NULL,
            last_seen TEXT,
            last_ip TEXT,
            last_mac TEXT,
            last_vlan TEXT,
            last_switch TEXT,
            last_port TEXT,
            state TEXT,
            display_name TEXT,
            updated_at TEXT NOT NULL,
            data_json TEXT
        );

        CREATE TABLE IF NOT EXISTS monitor_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time TEXT NOT NULL,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            subject_kind TEXT,
            subject_id TEXT,
            subject_name TEXT,
            message TEXT NOT NULL,
            details_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_monitor_events_time ON monitor_events(event_time DESC, id DESC);

        CREATE TABLE IF NOT EXISTS problem_state (
            problem_id TEXT PRIMARY KEY,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            acknowledged_at TEXT,
            data_json TEXT
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            summary_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(generated_at DESC);
        """
    )
    for column, column_type in (("mac_addresses", "TEXT"), ("device_type", "TEXT"), ("criticality", "TEXT"), ("asset_tag", "TEXT"), ("favorite", "INTEGER NOT NULL DEFAULT 0"), ("ignored", "INTEGER NOT NULL DEFAULT 0")):
        _ensure_column(connection, "device_metadata", column, column_type)
    _ensure_column(connection, "problem_state", "acknowledged_at", "TEXT")


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


# --------------------------------------------------------------------------- metadata


def mac_list(values: Any) -> list[str]:
    if values is None:
        raw: list[Any] = []
    elif isinstance(values, str):
        text = values.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                raw = parsed if isinstance(parsed, list) else [text]
            except json.JSONDecodeError:
                raw = [text]
        else:
            raw = [part for part in text.replace(",", "\n").replace(";", "\n").splitlines() if part.strip()]
    elif isinstance(values, (list, tuple, set)):
        raw = list(values)
    else:
        raw = [values]
    result: list[str] = []
    for value in raw:
        mac = normalize_mac(value)
        if mac and mac not in result:
            result.append(mac)
    return result


def _row_to_metadata(row: sqlite3.Row) -> dict[str, Any]:
    macs = mac_list(row["mac_addresses"])
    primary = normalize_mac(row["mac_address"])
    if primary and primary not in macs:
        macs.insert(0, primary)
    return {
        "identity_key": row["identity_key"],
        "mac_address": macs[0] if macs else (primary or ""),
        "mac_addresses": macs,
        "display_name": row["display_name"] or "",
        "owner": row["owner"] or "",
        "device_type": row["device_type"] or "",
        "criticality": row["criticality"] or "",
        "asset_tag": row["asset_tag"] or "",
        "notes": row["notes"] or "",
        "favorite": bool(row["favorite"]),
        "ignored": bool(row["ignored"]),
        "updated_at": row["updated_at"],
    }


def load_device_metadata() -> dict[str, dict[str, Any]]:
    """Return metadata indexed by identity key and by every MAC alias (``mac:<mac>``)."""
    with _LOCK, _connect() as connection:
        rows = connection.execute("SELECT * FROM device_metadata").fetchall()
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        metadata = _row_to_metadata(row)
        indexed[metadata["identity_key"]] = metadata
        for mac in metadata["mac_addresses"]:
            indexed.setdefault(f"mac:{mac}", metadata)
    return indexed


def save_device_metadata(mac_address: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    """Create or update a known device. Fields present in ``metadata`` are written
    verbatim, so an empty string clears a previously saved value."""
    now = _now()
    with _LOCK, _connect() as connection:
        records = [_row_to_metadata(row) for row in connection.execute("SELECT * FROM device_metadata").fetchall()]
        requested = str(metadata.get("identity_key") or "").strip()
        current_mac = normalize_mac(mac_address) or normalize_mac(metadata.get("mac_address"))
        existing = next((record for record in records if requested and record["identity_key"] == requested), None)
        if existing is None and current_mac:
            existing = next((record for record in records if current_mac in record["mac_addresses"]), None)
        existing = existing or {}
        identity_key = existing.get("identity_key") or (f"mac:{current_mac}" if current_mac else None)
        if not identity_key:
            raise ValueError("A valid MAC address is required to save device metadata.")

        if "mac_addresses" in metadata:
            macs = mac_list(metadata.get("mac_addresses"))
            if current_mac and current_mac not in macs and not existing:
                macs.insert(0, current_mac)
        else:
            macs = list(existing.get("mac_addresses") or [])
            if current_mac and current_mac not in macs:
                macs.append(current_mac)
        if not macs and current_mac:
            macs = [current_mac]
        if not macs:
            raise ValueError("At least one valid MAC address is required.")
        for record in records:
            if record["identity_key"] == identity_key:
                continue
            clash = set(record["mac_addresses"]).intersection(macs)
            if clash:
                raise ValueError("MAC address already belongs to another known device: " + ", ".join(sorted(clash)).upper())

        def field(name: str, *aliases: str) -> str:
            for candidate in (name, *aliases):
                if candidate in metadata:
                    return str(metadata.get(candidate) or "").strip()
            return str(existing.get(name) or "").strip()

        prepared = {
            "identity_key": identity_key,
            "mac_address": macs[0],
            "mac_addresses": macs,
            "display_name": field("display_name", "name"),
            "owner": field("owner"),
            "device_type": field("device_type", "category"),
            "criticality": field("criticality"),
            "asset_tag": field("asset_tag"),
            "notes": field("notes"),
            "favorite": bool(metadata["favorite"]) if "favorite" in metadata else bool(existing.get("favorite", False)),
            "ignored": bool(metadata["ignored"]) if "ignored" in metadata else bool(existing.get("ignored", False)),
            "updated_at": now,
        }
        connection.execute(
            """
            INSERT INTO device_metadata (identity_key, mac_address, mac_addresses, display_name, owner, device_type,
                criticality, asset_tag, notes, favorite, ignored, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(identity_key) DO UPDATE SET
                mac_address = excluded.mac_address, mac_addresses = excluded.mac_addresses,
                display_name = excluded.display_name, owner = excluded.owner, device_type = excluded.device_type,
                criticality = excluded.criticality, asset_tag = excluded.asset_tag, notes = excluded.notes,
                favorite = excluded.favorite, ignored = excluded.ignored, updated_at = excluded.updated_at
            """,
            (
                prepared["identity_key"], prepared["mac_address"], json.dumps(macs), prepared["display_name"], prepared["owner"],
                prepared["device_type"], prepared["criticality"], prepared["asset_tag"], prepared["notes"],
                1 if prepared["favorite"] else 0, 1 if prepared["ignored"] else 0, now,
            ),
        )
    return prepared


def delete_device_metadata(identity_key: str) -> bool:
    with _LOCK, _connect() as connection:
        cursor = connection.execute("DELETE FROM device_metadata WHERE identity_key = ?", (identity_key,))
        return cursor.rowcount > 0


# --------------------------------------------------------------------------- device records


def load_device_records() -> dict[str, dict[str, Any]]:
    with _LOCK, _connect() as connection:
        rows = connection.execute("SELECT * FROM device_records").fetchall()
    records: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            data = json.loads(row["data_json"] or "{}")
        except json.JSONDecodeError:
            data = {}
        records[row["key"]] = {
            "key": row["key"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "last_ip": row["last_ip"],
            "last_mac": row["last_mac"],
            "last_vlan": row["last_vlan"],
            "last_switch": row["last_switch"],
            "last_port": row["last_port"],
            "state": row["state"],
            "display_name": row["display_name"],
            "updated_at": row["updated_at"],
            **data,
        }
    return records


def save_device_records(records: list[dict[str, Any]]) -> None:
    now = _now()
    with _LOCK, _connect() as connection:
        connection.executemany(
            """
            INSERT INTO device_records (key, first_seen, last_seen, last_ip, last_mac, last_vlan, last_switch, last_port, state, display_name, updated_at, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                first_seen = COALESCE(device_records.first_seen, excluded.first_seen),
                last_seen = excluded.last_seen, last_ip = excluded.last_ip, last_mac = excluded.last_mac,
                last_vlan = excluded.last_vlan, last_switch = excluded.last_switch, last_port = excluded.last_port,
                state = excluded.state, display_name = excluded.display_name, updated_at = excluded.updated_at,
                data_json = excluded.data_json
            """,
            [
                (
                    record["key"], record.get("first_seen") or now, record.get("last_seen"), record.get("last_ip"), record.get("last_mac"),
                    str(record.get("last_vlan") or "") or None, record.get("last_switch"), str(record.get("last_port") or "") or None,
                    record.get("state"), record.get("display_name"), now, json.dumps(record.get("data") or {}),
                )
                for record in records
            ],
        )


def delete_device_record(key: str) -> None:
    with _LOCK, _connect() as connection:
        connection.execute("DELETE FROM device_records WHERE key = ?", (key,))


# --------------------------------------------------------------------------- events


def insert_events(events: list[dict[str, Any]]) -> None:
    if not events:
        return
    with _LOCK, _connect() as connection:
        connection.executemany(
            "INSERT INTO monitor_events (event_time, event_type, severity, subject_kind, subject_id, subject_name, message, details_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    event.get("time") or _now(), event.get("type") or "event", event.get("severity") or "info",
                    event.get("subject_kind"), event.get("subject_id"), event.get("subject_name"), event.get("message") or "",
                    json.dumps(event.get("details") or {}),
                )
                for event in events
            ],
        )
        connection.execute("DELETE FROM monitor_events WHERE id NOT IN (SELECT id FROM monitor_events ORDER BY event_time DESC, id DESC LIMIT 2000)")


def recent_events(limit: int = 60) -> list[dict[str, Any]]:
    with _LOCK, _connect() as connection:
        rows = connection.execute("SELECT * FROM monitor_events ORDER BY event_time DESC, id DESC LIMIT ?", (limit,)).fetchall()
    events = []
    for row in rows:
        try:
            details = json.loads(row["details_json"] or "{}")
        except json.JSONDecodeError:
            details = {}
        events.append(
            {
                "id": row["id"],
                "time": row["event_time"],
                "type": row["event_type"],
                "severity": row["severity"],
                "subject_kind": row["subject_kind"],
                "subject_id": row["subject_id"],
                "subject_name": row["subject_name"],
                "message": row["message"],
                "details": details,
            }
        )
    return events


# --------------------------------------------------------------------------- problems


def load_problem_state() -> dict[str, dict[str, Any]]:
    with _LOCK, _connect() as connection:
        rows = connection.execute("SELECT * FROM problem_state").fetchall()
    return {row["problem_id"]: {"problem_id": row["problem_id"], "first_seen": row["first_seen"], "last_seen": row["last_seen"], "severity": row["severity"], "title": row["title"], "active": bool(row["active"]), "acknowledged_at": row["acknowledged_at"]} for row in rows}


def save_problem_state(problems: list[dict[str, Any]], cleared_ids: list[str]) -> None:
    with _LOCK, _connect() as connection:
        connection.executemany(
            """
            INSERT INTO problem_state (problem_id, first_seen, last_seen, severity, title, active, acknowledged_at, data_json)
            VALUES (?, ?, ?, ?, ?, 1, NULL, ?)
            ON CONFLICT(problem_id) DO UPDATE SET
                first_seen = COALESCE(problem_state.first_seen, excluded.first_seen),
                last_seen = excluded.last_seen, severity = excluded.severity, title = excluded.title, active = 1, data_json = excluded.data_json
            """,
            [(problem["id"], problem.get("first_seen") or _now(), problem.get("last_seen") or _now(), problem.get("severity") or "info", problem.get("title") or "", json.dumps({k: v for k, v in problem.items() if k in {"category", "detail", "affected"}})) for problem in problems],
        )
        if cleared_ids:
            connection.executemany("UPDATE problem_state SET active = 0 WHERE problem_id = ?", [(problem_id,) for problem_id in cleared_ids])
        connection.execute("DELETE FROM problem_state WHERE active = 0 AND last_seen < datetime('now', '-14 days')")


def acknowledge_problem(problem_id: str, acknowledged: bool) -> None:
    with _LOCK, _connect() as connection:
        connection.execute("UPDATE problem_state SET acknowledged_at = ? WHERE problem_id = ?", (_now() if acknowledged else None, problem_id))


# --------------------------------------------------------------------------- snapshots


def record_snapshot(summary: dict[str, Any]) -> None:
    with _LOCK, _connect() as connection:
        connection.execute("INSERT INTO snapshots (generated_at, summary_json) VALUES (?, ?)", (_now(), json.dumps(summary, sort_keys=True)))
        connection.execute("DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY generated_at DESC, id DESC LIMIT 720)")


def snapshot_history(limit: int = 120) -> list[dict[str, Any]]:
    with _LOCK, _connect() as connection:
        rows = connection.execute("SELECT generated_at, summary_json FROM snapshots ORDER BY generated_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    result = []
    for row in rows:
        try:
            result.append({"generated_at": row["generated_at"], **json.loads(row["summary_json"])})
        except json.JSONDecodeError:
            continue
    return list(reversed(result))

"""The monitoring engine: scheduler, scan lock, cooldowns, cache and event bus.

The backend is the single source of truth. Browsers only read ``/api/state``
and subscribe to ``/api/events``; every poll, sweep and timestamp originates
here. One cycle runs at a time (``_scan_lock``); requests that arrive while a
cycle is running are queued (one slot, ``full`` beats ``fast``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from typing import Any

from . import snmp as snmp_module
from .access_points import build_access_point_views
from .checks import ONLINE, OFFLINE, UNKNOWN, ping_many, run_check, utc_now
from .config import ConfigError, load_normalized
from .devices import ACTIVE_LOCATED, ACTIVE_UNLOCATED, DeviceEngine, device_record, seconds_between
from .discovery import discover_subnets
from .neighbors import local_neighbors
from .problems import detect_problems
from .storage import (
    insert_events,
    load_device_metadata,
    load_device_records,
    load_problem_state,
    record_snapshot,
    recent_events,
    save_device_records,
    save_problem_state,
    snapshot_history,
)
from .switches import build_switch_view
from .topology import build_topology


log = logging.getLogger("aboutus.monitor")
VERSION = "1.0.0"


class Monitor:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self.scan: dict[str, Any] = {
            "state": "idle",
            "mode": None,
            "phase": "idle",
            "started_at": None,
            "finished_at": None,
            "last_success_at": None,
            "last_error": None,
            "last_error_at": None,
            "requested_by": None,
            "queued_mode": None,
            "cycle": 0,
            "last_duration_ms": None,
            "phase_started_at": None,
        }
        self._scan_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._scheduler: asyncio.Task | None = None
        self._queued: tuple[str, dict[str, Any]] | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._event_seq = 0
        self.errors: list[dict[str, Any]] = []
        self.previous_devices: dict[str, dict[str, Any]] = {}
        self.problem_state: dict[str, dict[str, Any]] = {}
        self.discovery_cache: dict[str, Any] = {"status": "not_run", "hosts": [], "subnets": [], "errors": [], "finished_at": None, "tool": None}
        self.last_full_ts = 0.0
        self.last_fast_ts = 0.0
        self.last_full_at: str | None = None
        self.cooldown_until = 0.0
        self.targeted_cooldown_until = 0.0
        self.cycle_count = 0
        self.port_state: dict[tuple[str, int], str] = {}
        self.infra_state: dict[str, str] = {}
        self.pending_targeted_vlans: set[int] = set()
        self.started_at = utc_now()
        self.config_error: str | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        try:
            self.previous_devices = load_device_records()
            self.problem_state = load_problem_state()
        except Exception as exc:  # pragma: no cover - defensive
            self._record_error(f"Could not load persisted state: {exc}")
        if self._scheduler is None or self._scheduler.done():
            self._scheduler = asyncio.create_task(self._scheduler_loop(), name="monitor-scheduler")

    async def stop(self) -> None:
        for task in (self._scheduler, self._task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._scheduler = None
        self._task = None

    # ------------------------------------------------------------------ events

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event_type: str, data: Any) -> None:
        self._event_seq += 1
        event = {"id": self._event_seq, "type": event_type, "timestamp": utc_now(), "data": data}
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except Exception:
                    self._subscribers.discard(queue)

    def _record_error(self, message: str, *, publish: bool = True) -> None:
        log.error(message)
        entry = {"time": utc_now(), "message": message}
        self.errors.insert(0, entry)
        del self.errors[20:]
        self.scan["last_error"] = message
        self.scan["last_error_at"] = entry["time"]
        if publish:
            self.publish("error", {"message": message, "time": entry["time"], "scan": self.scan_status()})

    # ------------------------------------------------------------------ status

    def _settings(self) -> dict[str, Any]:
        try:
            return load_normalized()["monitoring"]
        except ConfigError:
            from .config import DEFAULT_MONITORING

            return dict(DEFAULT_MONITORING)

    def scan_status(self) -> dict[str, Any]:
        now = time.monotonic()
        settings = self._settings()
        running = bool(self._task and not self._task.done()) or self.scan.get("state") == "running"
        status = dict(self.scan)
        status["state"] = "running" if running else "idle"
        if not running and status.get("phase") not in {"idle", "error", "cancelled"}:
            status["phase"] = "idle"
        status["cooldown_remaining_seconds"] = round(max(0.0, self.cooldown_until - now), 1)
        status["cooldown_seconds"] = settings["refresh_cooldown_seconds"]
        status["queued_mode"] = self._queued[0] if self._queued else None
        status["next_fast_in_seconds"] = round(max(0.0, self.last_fast_ts + settings["fast_poll_interval_seconds"] - now), 1) if self.last_fast_ts else 0
        status["next_full_in_seconds"] = round(max(0.0, self.last_full_ts + settings["full_scan_interval_seconds"] - now), 1) if self.last_full_ts else 0
        status["full_scan_available_in_seconds"] = round(max(0.0, self.last_full_ts + settings["full_scan_min_interval_seconds"] - now), 1) if self.last_full_ts else 0
        status["last_full_at"] = self.last_full_at
        status["fast_interval_seconds"] = settings["fast_poll_interval_seconds"]
        status["full_interval_seconds"] = settings["full_scan_interval_seconds"]
        status["generated_at"] = self.state.get("generated_at")
        status["data_age_seconds"] = round(seconds_between(utc_now(), self.state.get("generated_at")) or 0, 1) if self.state.get("generated_at") else None
        status["has_state"] = bool(self.state)
        status["errors"] = self.errors[:5]
        status["uptime_since"] = self.started_at
        status["version"] = VERSION
        return status

    def snapshot(self) -> dict[str, Any]:
        if not self.state:
            return {"generated_at": None, "version": VERSION, "scan": self.scan_status(), "ready": False, "config_error": self.config_error, "infrastructure": [], "access_points": [], "switches": [], "devices": [], "device_observations": {"mac_only": []}, "topology": {"nodes": [], "links": []}, "problems": [], "problem_counts": {"critical": 0, "warning": 0, "info": 0}, "vlans": [], "internet": {"status": UNKNOWN, "probes": []}, "history": {"events": []}, "settings": self._settings_view()}
        return {**self.state, "scan": self.scan_status(), "ready": True, "config_error": self.config_error}

    def _settings_view(self) -> dict[str, Any]:
        from .settings import settings_view

        try:
            return settings_view()
        except ConfigError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------ requests

    async def request_scan(self, mode: str = "fast", source: str = "manual") -> dict[str, Any]:
        mode = "full" if str(mode).lower() == "full" else "fast"
        settings = self._settings()
        now = time.monotonic()
        message = None
        async with self._request_lock:
            if mode == "full" and self.last_full_ts and now - self.last_full_ts < settings["full_scan_min_interval_seconds"]:
                wait = round(self.last_full_ts + settings["full_scan_min_interval_seconds"] - now)
                mode = "fast"
                message = f"Full discovery ran {round(now - self.last_full_ts)}s ago; running a fast poll instead (full available in {wait}s)."
            if self._task and not self._task.done():
                if source == "scheduler":
                    return {"accepted": False, "queued": False, "cooldown": False, "message": "A cycle is already running.", "scan": self.scan_status()}
                if not self._queued or (mode == "full" and self._queued[0] != "full"):
                    self._queued = (mode, {"source": source})
                self.scan["requested_by"] = source
                self.publish("scan_status", self.scan_status())
                return {"accepted": True, "queued": True, "cooldown": False, "message": message or f"{mode} scan queued behind the running cycle.", "scan": self.scan_status()}
            if source != "scheduler" and now < self.cooldown_until:
                return {"accepted": False, "queued": False, "cooldown": True, "retry_in_seconds": round(self.cooldown_until - now, 1), "message": f"Refresh cooldown: try again in {round(self.cooldown_until - now, 1)}s.", "scan": self.scan_status()}
            self.scan["requested_by"] = source
            self._start_cycle(mode, {"source": source})
            return {"accepted": True, "queued": False, "cooldown": False, "message": message or f"{mode} scan started.", "scan": self.scan_status()}

    def _start_cycle(self, mode: str, options: dict[str, Any]) -> None:
        self.scan.update({"state": "running", "mode": mode, "phase": "queued", "started_at": utc_now(), "finished_at": None, "phase_started_at": utc_now()})
        self._task = asyncio.create_task(self._run_cycle(mode, options), name=f"monitor-cycle-{mode}")
        self.publish("scan_status", self.scan_status())

    async def _scheduler_loop(self) -> None:
        await asyncio.sleep(0.5)
        last_heartbeat = time.monotonic()
        while True:
            try:
                settings = self._settings()
                now = time.monotonic()
                if self._task and not self._task.done():
                    pass
                elif self._queued:
                    mode, options = self._queued
                    self._queued = None
                    if mode == "full" and self.last_full_ts and now - self.last_full_ts < settings["full_scan_min_interval_seconds"]:
                        mode = "fast"
                    self._start_cycle(mode, options)
                elif not self.state:
                    self._start_cycle("full", {"source": "startup"})
                elif not self.last_full_ts or now - self.last_full_ts >= settings["full_scan_interval_seconds"]:
                    self._start_cycle("full", {"source": "scheduler"})
                elif now - self.last_fast_ts >= settings["fast_poll_interval_seconds"]:
                    self._start_cycle("fast", {"source": "scheduler"})
                if now - last_heartbeat >= 15:
                    last_heartbeat = now
                    self.publish("heartbeat", {"scan": self.scan_status()})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                self._record_error(f"Scheduler error: {exc}")
            await asyncio.sleep(1)

    def reload_config(self) -> None:
        """Called after settings changes: drop SNMP static caches and poll soon."""
        snmp_module.forget_static()
        self.last_fast_ts = 0.0
        if self.state:
            self.state["settings"] = self._settings_view()
            self.publish("state_snapshot", self.snapshot())

    # ------------------------------------------------------------------ cycle

    def _phase(self, phase: str) -> None:
        self.scan["phase"] = phase
        self.scan["phase_started_at"] = utc_now()
        self.publish("scan_status", self.scan_status())

    async def _run_cycle(self, mode: str, options: dict[str, Any]) -> None:
        started = time.monotonic()
        try:
            async with self._scan_lock:
                await self._cycle(mode, options)
            finished = utc_now()
            self.scan.update({"state": "idle", "phase": "idle", "finished_at": finished, "last_success_at": finished, "last_error": None, "last_duration_ms": round((time.monotonic() - started) * 1000)})
            self.cooldown_until = time.monotonic() + self._settings()["refresh_cooldown_seconds"]
        except asyncio.CancelledError:
            self.scan.update({"state": "idle", "phase": "cancelled", "finished_at": utc_now()})
            raise
        except Exception as exc:
            log.debug(traceback.format_exc())
            self.scan.update({"state": "idle", "phase": "error", "finished_at": utc_now(), "last_duration_ms": round((time.monotonic() - started) * 1000)})
            self._record_error(f"{mode} cycle failed: {type(exc).__name__}: {exc}")
        finally:
            self.publish("scan_status", self.scan_status())

    async def _cycle(self, mode: str, options: dict[str, Any]) -> None:
        cycle_started = time.monotonic()
        try:
            config = load_normalized()
            self.config_error = None
        except ConfigError as exc:
            self.config_error = str(exc)
            raise
        settings = config["monitoring"]
        raw = config["raw"]
        now = utc_now()
        self.cycle_count += 1
        self.scan["cycle"] = self.cycle_count
        targeted_vlans = set(options.get("vlan_ids") or []) if mode == "targeted" else None

        infra = self._infra_list(config)
        infra_by_id = {item["id"]: item for item in infra}

        # ---- phase 1: reachability --------------------------------------------------
        self._phase("checks")
        defaults = config.get("check_defaults") or {}
        timeout = float(defaults.get("timeout_seconds") or 1.0)
        known_ips = [record.get("ip_address") for record in self.previous_devices.values() if record.get("ip_address")]
        gateway_ips = [vlan["gateway"] for vlan in config["vlans"] if vlan.get("gateway")]
        probe_targets = [item["ip_address"] for item in infra if item.get("ip_address") and item.get("enabled", True)] + gateway_ips + known_ips[:512]
        ping_results, internet = await asyncio.gather(ping_many(probe_targets, timeout, int(defaults.get("ping_count") or 1)), self._internet_status(config, defaults))
        for item in infra:
            check = ping_results.get(item.get("ip_address") or "")
            if not item.get("enabled", True):
                item["status"] = "disabled"
                item["check"] = None
            elif check:
                item["status"] = check["status"]
                item["check"] = check
            else:
                item["status"] = UNKNOWN
                item["check"] = None
        vlan_status = []
        for vlan in config["vlans"]:
            check = ping_results.get(vlan["gateway"] or "") if vlan.get("gateway") else None
            vlan_status.append({**vlan, "gateway_status": check["status"] if check else ("n/a" if not vlan.get("gateway") else UNKNOWN), "gateway_check": check})

        # ---- phase 2: SNMP -----------------------------------------------------------------
        self._phase("snmp")
        include_static = mode == "full" or (self.cycle_count % settings["static_tables_every_cycles"] == 0)
        snmp_targets = [item for item in infra if item.get("kind") in {"switch", "router", "ap"} and item.get("snmp_enabled") and item.get("enabled", True) and item.get("status") != OFFLINE]
        snmp = await snmp_module.poll_devices(snmp_targets, raw, include_static=include_static)
        snmp_devices = snmp.get("devices") or {}
        for item in infra:
            result = snmp_devices.get(item["id"])
            item["snmp"] = {"status": result.get("status") if result else ("disabled" if not item.get("snmp_enabled") else ("skipped" if item.get("status") == OFFLINE else "pending")), "error": (result or {}).get("error"), "last_poll": (result or {}).get("last_poll"), "sys_name": ((result or {}).get("sys") or {}).get("name"), "sys_descr": ((result or {}).get("sys") or {}).get("descr"), "sys_location": ((result or {}).get("sys") or {}).get("location"), "sys_contact": ((result or {}).get("sys") or {}).get("contact"), "uptime": ((result or {}).get("sys") or {}).get("uptime"), "uptime_seconds": ((result or {}).get("sys") or {}).get("uptime_seconds"), "duration_ms": (result or {}).get("duration_ms"), "vendor_profile": (result or {}).get("vendor_profile")}
            if result and result.get("status") == "ok":
                item["sys_name"] = (result.get("sys") or {}).get("name")
                interfaces = (result.get("interfaces") or {}).values()
                item["ports_up"] = sum(1 for i in interfaces if i.get("oper_status") == "up")
                item["port_count_snmp"] = len(result.get("interfaces") or {})

        # port change detection -> targeted refresh
        changed_ports: list[dict[str, Any]] = []
        for device_id, result in snmp_devices.items():
            if result.get("status") != "ok":
                continue
            for interface in (result.get("interfaces") or {}).values():
                port = interface.get("port")
                if port is None:
                    continue
                key = (device_id, int(port))
                previous = self.port_state.get(key)
                current = interface.get("oper_status") or "unknown"
                if previous is not None and previous != current:
                    changed_ports.append({"switch_id": device_id, "port": int(port), "from": previous, "to": current, "name": interface.get("name")})
                self.port_state[key] = current

        # ---- phase 3: discovery --------------------------------------------------------
        if mode == "full" or (mode == "targeted" and targeted_vlans):
            self._phase("discovery")
            result = await discover_subnets(raw, config["vlans"], only_vlan_ids=targeted_vlans)
            if mode == "full":
                self.discovery_cache = {key: value for key, value in result.items()}
                self.last_full_ts = time.monotonic()
                self.last_full_at = utc_now()
            else:
                kept = [host for host in self.discovery_cache.get("hosts") or [] if host.get("vlan_id") not in targeted_vlans]
                self.discovery_cache["hosts"] = kept + list(result.get("hosts") or [])
                self.discovery_cache["targeted_at"] = utc_now()
                self.discovery_cache["targeted_vlans"] = sorted(targeted_vlans)
        discovery = self.discovery_cache

        # ---- phase 4: correlation --------------------------------------------------------
        self._phase("correlate")
        neighbors = await local_neighbors()
        metadata = load_device_metadata()
        engine = DeviceEngine(config)
        built = engine.build(now=now, infra=infra, snmp_devices=snmp_devices, neighbors=neighbors, discovery_hosts=discovery.get("hosts") or [], ping=ping_results, previous=self.previous_devices, metadata=metadata, inventory=config["inventory"])
        devices = built["devices"]
        mac_only = built["mac_only"]
        infra_macs = built["infra_macs"]
        fdb_by_mac = built["fdb_by_mac"]

        # ---- phase 5: switches / topology / problems -----------------------------------
        self._phase("analyze")
        devices_by_switch_port: dict[str, dict[int, list[dict[str, Any]]]] = {}
        for device in devices + mac_only:
            location = device.get("location") or {}
            if location.get("switch_id") and location.get("port") is not None and location.get("confidence") in {"edge", "edge_unconfigured"}:
                devices_by_switch_port.setdefault(location["switch_id"], {}).setdefault(int(location["port"]), []).append(device)
        infra_by_switch_port: dict[str, dict[int, list[dict[str, Any]]]] = {}
        for mac, infra_id in infra_macs.items():
            for observation in fdb_by_mac.get(mac) or []:
                if observation["switch_id"] == infra_id or observation.get("port") is None:
                    continue
                bucket = infra_by_switch_port.setdefault(observation["switch_id"], {}).setdefault(int(observation["port"]), [])
                if not any(entry["id"] == infra_id for entry in bucket):
                    bucket.append({"id": infra_id, "name": infra_by_id.get(infra_id, {}).get("name", infra_id), "kind": infra_by_id.get(infra_id, {}).get("kind"), "evidence": "fdb"})
        neighbor_flags = {item["id"]: {"name": item.get("name"), "optional": item.get("optional"), "planned": item.get("planned"), "enabled": item.get("enabled", True)} for item in infra}
        switch_views = [build_switch_view(switch, infra_by_id.get(switch["id"], {}), snmp_devices.get(switch["id"]), devices_by_switch_port.get(switch["id"], {}), infra_by_switch_port.get(switch["id"], {}), config, neighbor_flags) for switch in config["switches"] if switch.get("enabled", True)]
        topology = build_topology(infra=infra, switch_views=switch_views, snmp_devices=snmp_devices, infra_macs=infra_macs, fdb_by_mac=fdb_by_mac, devices=devices, internet=internet, config=config)
        access_points = build_access_point_views(config=config, infra=infra, snmp_devices=snmp_devices, switch_views=switch_views, topology=topology, now=now)
        problems = detect_problems(config=config, infra=infra, switch_views=switch_views, topology=topology, devices=devices, snmp=snmp, vlan_status=vlan_status, internet=internet, discovery=discovery, ip_mac=built["ip_mac"], access_points=access_points)
        problems, cleared = self._track_problems(problems, now)
        for view in switch_views:
            view["problem_count"] = sum(1 for problem in problems if problem["affected"].get("id") == view["id"] or problem["affected"].get("switch_id") == view["id"])
        for item in infra:
            item["problem_count"] = sum(1 for problem in problems if problem["affected"].get("id") == item["id"] and problem["severity"] != "info")

        # ---- phase 6: persistence + events ---------------------------------------------------
        self._phase("persist")
        events = self._device_events(devices, now)
        events.extend(self._infra_events(infra, now))
        events.extend({"time": now, "type": "port_change", "severity": "warning" if change["to"] != "up" else "info", "subject_kind": "port", "subject_id": f"{change['switch_id']}:{change['port']}", "subject_name": f"{infra_by_id.get(change['switch_id'], {}).get('name', change['switch_id'])} port {change['port']}", "message": f"{infra_by_id.get(change['switch_id'], {}).get('name', change['switch_id'])} port {change['port']} went {change['to']} (was {change['from']})", "details": change} for change in changed_ports)
        events.extend(self._problem_events(problems, cleared, now, infra_by_id))
        try:
            insert_events(events)
            save_device_records([device_record(device) for device in devices + mac_only])
            save_problem_state(problems, [state["problem_id"] for state in cleared])
            if mode == "full" or self.cycle_count % 5 == 0:
                record_snapshot({"devices": len(devices), "active": sum(1 for d in devices if d["state"] in {ACTIVE_LOCATED, ACTIVE_UNLOCATED}), "located": sum(1 for d in devices if d["state"] == ACTIVE_LOCATED), "problems_critical": sum(1 for p in problems if p["severity"] == "critical"), "problems_warning": sum(1 for p in problems if p["severity"] == "warning"), "infra_online": sum(1 for i in infra if i.get("status") == ONLINE), "infra_total": sum(1 for i in infra if i.get("enabled", True) and not i.get("planned"))})
        except Exception as exc:
            self._record_error(f"Database write failed: {exc}", publish=False)
        self.previous_devices = {device["key"]: {**device_record(device), **device_record(device)["data"]} for device in devices + mac_only}

        # ---- snapshot ----------------------------------------------------------------------
        generated_at = utc_now()
        counts = self._device_counts(devices, mac_only)
        problem_counts = {"critical": sum(1 for p in problems if p["severity"] == "critical"), "warning": sum(1 for p in problems if p["severity"] == "warning"), "info": sum(1 for p in problems if p["severity"] == "info"), "acknowledged": sum(1 for p in problems if p.get("acknowledged_at"))}
        previous_state = self.state
        self.state = {
            "generated_at": generated_at,
            "version": VERSION,
            "mode": mode,
            "cycle": self.cycle_count,
            "cycle_duration_ms": round((time.monotonic() - cycle_started) * 1000),
            "station": config["station"],
            "infrastructure": [self._infra_public(item) for item in infra],
            "vlans": [{**vlan, "gateway_check": vlan.get("gateway_check"), "device_counts": self._vlan_counts(vlan["id"], devices)} for vlan in vlan_status],
            "internet": internet,
            "devices": devices,
            "device_observations": {"mac_only": mac_only},
            "device_counts": counts,
            "access_points": access_points,
            "switches": switch_views,
            "topology": topology,
            "problems": problems,
            "problem_counts": problem_counts,
            "discovery": {key: value for key, value in discovery.items() if key != "hosts"} | {"host_count": len(discovery.get("hosts") or [])},
            "snmp": {"enabled": snmp.get("enabled"), "status": snmp.get("status"), "errors": snmp.get("errors") or [], "include_static": include_static},
            "history": {"events": recent_events(80), "snapshots": snapshot_history(120)},
            "colors": config["colors"],
            "policy": config["policy"],
            "port_profiles": config["port_profiles"],
            "settings": self._settings_view(),
            "errors": self.errors[:5],
        }
        self.last_fast_ts = time.monotonic()

        # ---- publish ----------------------------------------------------------------------------
        scan = self.scan_status()
        self.publish("device_update", {"generated_at": generated_at, "devices": devices, "device_observations": {"mac_only": mac_only}, "device_counts": counts, "access_points": access_points, "vlans": self.state["vlans"], "infrastructure": self.state["infrastructure"], "internet": internet, "history": self.state["history"], "discovery": self.state["discovery"], "snmp": self.state["snmp"], "cycle": self.cycle_count, "mode": mode, "cycle_duration_ms": self.state["cycle_duration_ms"], "errors": self.errors[:5]})
        self.publish("switch_update", {"generated_at": generated_at, "switches": switch_views})
        if changed_ports:
            self.publish("port_update", {"generated_at": generated_at, "changes": changed_ports})
        if json.dumps(topology, sort_keys=True, default=str) != json.dumps(previous_state.get("topology"), sort_keys=True, default=str):
            self.publish("topology_update", {"generated_at": generated_at, "topology": topology})
        if json.dumps([(p["id"], p["severity"], p["acknowledged_at"]) for p in problems]) != json.dumps([(p["id"], p["severity"], p.get("acknowledged_at")) for p in previous_state.get("problems") or []]):
            self.publish("problem_update", {"generated_at": generated_at, "problems": problems, "problem_counts": problem_counts})
        if not previous_state:
            self.publish("state_snapshot", self.snapshot())

        # ---- targeted follow-up ---------------------------------------------------------------
        if changed_ports and mode != "targeted":
            vlan_ids: set[int] = set()
            switch_config = {switch["id"]: switch for switch in config["switches"]}
            for change in changed_ports:
                port = (switch_config.get(change["switch_id"]) or {}).get("ports", {}).get(change["port"]) or {}
                if port.get("type") == "access" and port.get("vlan") is not None:
                    vlan_ids.add(int(port["vlan"]))
                elif port.get("type") in {"trunk", "ap_trunk"}:
                    vlan_ids.update(int(v) for v in port.get("tagged") or [])
            self.pending_targeted_vlans |= {vlan_id for vlan_id in vlan_ids if any(v["id"] == vlan_id and v.get("monitor") for v in config["vlans"])}
        if self.pending_targeted_vlans and time.monotonic() >= self.targeted_cooldown_until and mode != "targeted" and not self._queued:
            self._queued = ("targeted", {"source": "port_change", "vlan_ids": sorted(self.pending_targeted_vlans)})
            self.targeted_cooldown_until = time.monotonic() + settings["targeted_refresh_cooldown_seconds"]
            self.pending_targeted_vlans = set()

    # ------------------------------------------------------------------ helpers

    def _infra_list(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if config.get("router"):
            items.append(dict(config["router"]))
        for switch in config["switches"]:
            items.append({key: value for key, value in switch.items() if key not in {"ports", "raw_ports"}})
        for ap in config["access_points"]:
            items.append(dict(ap))
        items.append(dict(config["station"]))
        return items

    @staticmethod
    def _infra_public(item: dict[str, Any]) -> dict[str, Any]:
        public = {key: value for key, value in item.items() if key not in {"snmp"}}
        public["snmp"] = {key: value for key, value in (item.get("snmp") or {}).items() if key != "community"}
        public["snmp_configured"] = bool((item.get("snmp") or {}).get("community") or (item.get("snmp") or {}).get("community_env")) if isinstance(item.get("snmp"), dict) else None
        return public

    async def _internet_status(self, config: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        probes = (config.get("internet") or {}).get("probes") or []
        if not probes:
            return {"status": UNKNOWN, "probes": []}

        async def run_probe(probe: dict[str, Any]) -> dict[str, Any]:
            check = await run_check(probe, defaults=defaults)
            return {"name": probe.get("name") or probe.get("target"), "target": probe.get("target"), "status": check["status"], "latency_ms": check.get("latency_ms"), "message": check.get("message")}

        results = await asyncio.gather(*(run_probe(probe) for probe in probes))
        if any(result["status"] == ONLINE for result in results):
            status = ONLINE
        elif any(result["status"] == OFFLINE for result in results):
            status = OFFLINE
        else:
            status = UNKNOWN
        return {"status": status, "probes": results, "checked_at": utc_now()}

    @staticmethod
    def _device_counts(devices: list[dict[str, Any]], mac_only: list[dict[str, Any]]) -> dict[str, int]:
        counts = {"total": len(devices), "active_located": 0, "active_unlocated": 0, "relocating": 0, "stale": 0, "offline": 0, "mac_only": len(mac_only), "ignored": 0, "favorite": 0, "unknown": 0, "infrastructure": 0}
        for device in devices:
            counts[device["state"]] = counts.get(device["state"], 0) + 1
            if device.get("ignored"):
                counts["ignored"] += 1
            if device.get("favorite"):
                counts["favorite"] += 1
            if device.get("is_infrastructure"):
                counts["infrastructure"] += 1
            elif not device.get("metadata", {}).get("saved") and not device.get("inventory") and device["state"] in {ACTIVE_LOCATED, ACTIVE_UNLOCATED}:
                counts["unknown"] += 1
        return counts

    @staticmethod
    def _vlan_counts(vlan_id: int, devices: list[dict[str, Any]]) -> dict[str, int]:
        members = [device for device in devices if device.get("vlan_id") == vlan_id]
        return {"total": len(members), "active": sum(1 for d in members if d["state"] in {ACTIVE_LOCATED, ACTIVE_UNLOCATED}), "located": sum(1 for d in members if d["state"] == ACTIVE_LOCATED), "offline": sum(1 for d in members if d["state"] == "offline"), "stale": sum(1 for d in members if d["state"] in {"stale", "relocating"})}

    def _track_problems(self, problems: list[dict[str, Any]], now: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        active_ids = {problem["id"] for problem in problems}
        cleared = [dict(state) for problem_id, state in self.problem_state.items() if state.get("active") and problem_id not in active_ids]
        for problem in problems:
            state = self.problem_state.get(problem["id"])
            if state and state.get("active"):
                problem["first_seen"] = state.get("first_seen") or now
                problem["acknowledged_at"] = state.get("acknowledged_at")
                problem["new"] = False
            else:
                problem["first_seen"] = now
                problem["acknowledged_at"] = None
                problem["new"] = True
            problem["last_seen"] = now
            self.problem_state[problem["id"]] = {"problem_id": problem["id"], "first_seen": problem["first_seen"], "last_seen": now, "severity": problem["severity"], "title": problem["title"], "active": True, "acknowledged_at": problem["acknowledged_at"]}
        for state in cleared:
            self.problem_state[state["problem_id"]] = {**state, "active": False}
        return problems, cleared

    def acknowledge(self, problem_id: str, acknowledged: bool) -> dict[str, Any] | None:
        from .storage import acknowledge_problem

        state = self.problem_state.get(problem_id)
        if not state:
            return None
        state["acknowledged_at"] = utc_now() if acknowledged else None
        acknowledge_problem(problem_id, acknowledged)
        for problem in self.state.get("problems") or []:
            if problem["id"] == problem_id:
                problem["acknowledged_at"] = state["acknowledged_at"]
        if self.state:
            problems = self.state.get("problems") or []
            self.state["problem_counts"]["acknowledged"] = sum(1 for p in problems if p.get("acknowledged_at"))
            self.publish("problem_update", {"generated_at": self.state.get("generated_at"), "problems": problems, "problem_counts": self.state["problem_counts"]})
        return state

    def _device_events(self, devices: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for device in devices:
            if device.get("is_infrastructure"):
                continue
            previous = self.previous_devices.get(device["key"])
            name = device["display_name"]
            subject = {"subject_kind": "device", "subject_id": device["key"], "subject_name": name}
            vlan = f"VLAN {device['vlan_id']}" if device.get("vlan_id") else "unknown VLAN"
            location = device.get("location") or {}
            if previous is None:
                if device["state"] in {ACTIVE_LOCATED, ACTIVE_UNLOCATED}:
                    where = f" on {location['switch_name']} port {location['port']}" if location.get("switch_id") and location.get("confidence", "").startswith("edge") else ""
                    events.append({"time": now, "type": "device_joined", "severity": "info", **subject, "message": f"{name} joined {vlan}{where} ({device.get('ip_address') or device.get('mac_address')})", "details": {"ip": device.get("ip_address"), "mac": device.get("mac_address")}})
                continue
            if previous.get("state") != device["state"]:
                severity = "warning" if device["state"] == "offline" and device.get("expected") else "info"
                events.append({"time": now, "type": "device_state", "severity": severity, **subject, "message": f"{name} is now {device['state_label'].lower()} (was {str(previous.get('state') or 'unknown').replace('_', ' ')})", "details": {"from": previous.get("state"), "to": device["state"], "ip": device.get("ip_address")}})
            if previous.get("ip_address") and device.get("ip_address") and previous["ip_address"] != device["ip_address"]:
                events.append({"time": now, "type": "device_ip_changed", "severity": "info", **subject, "message": f"{name} changed IP {previous['ip_address']} → {device['ip_address']}", "details": {"from": previous["ip_address"], "to": device["ip_address"]}})
            if location.get("confidence", "").startswith("edge") and previous.get("switch_id") and (previous.get("switch_id") != location.get("switch_id") or str(previous.get("port")) != str(location.get("port"))):
                events.append({"time": now, "type": "device_moved", "severity": "info", **subject, "message": f"{name} moved from {previous.get('switch_name') or previous.get('switch_id')} port {previous.get('port')} to {location.get('switch_name')} port {location.get('port')}", "details": {"from": [previous.get("switch_id"), previous.get("port")], "to": [location.get("switch_id"), location.get("port")]}})
            if previous.get("vlan_id") and device.get("vlan_id") and previous["vlan_id"] != device["vlan_id"]:
                events.append({"time": now, "type": "device_vlan_changed", "severity": "warning", **subject, "message": f"{name} moved from VLAN {previous['vlan_id']} to VLAN {device['vlan_id']}", "details": {"from": previous["vlan_id"], "to": device["vlan_id"]}})
        return events

    def _infra_events(self, infra: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for item in infra:
            status = item.get("status")
            previous = self.infra_state.get(item["id"])
            if previous is not None and previous != status and status in {ONLINE, OFFLINE}:
                severity = "critical" if status == OFFLINE and not (item.get("optional") or item.get("planned")) else ("warning" if status == OFFLINE else "info")
                events.append({"time": now, "type": "infra_status", "severity": severity, "subject_kind": item.get("kind"), "subject_id": item["id"], "subject_name": item["name"], "message": f"{item['name']} is {status}" + (f" (was {previous})" if previous else ""), "details": {"from": previous, "to": status}})
            self.infra_state[item["id"]] = status
        return events

    @staticmethod
    def _problem_events(problems: list[dict[str, Any]], cleared: list[dict[str, Any]], now: str, infra_by_id: dict[str, Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for problem in problems:
            if problem.get("new") and problem["severity"] != "info":
                events.append({"time": now, "type": "problem_raised", "severity": problem["severity"], "subject_kind": problem["affected"].get("kind"), "subject_id": problem["affected"].get("id"), "subject_name": problem["affected"].get("name"), "message": f"Problem: {problem['title']}", "details": {"problem_id": problem["id"], "category": problem["category"]}})
        for state in cleared:
            if state.get("severity") != "info":
                events.append({"time": now, "type": "problem_cleared", "severity": "info", "subject_kind": "problem", "subject_id": state["problem_id"], "subject_name": state.get("title"), "message": f"Resolved: {state.get('title')}", "details": {"problem_id": state["problem_id"]}})
        return events


monitor = Monitor()

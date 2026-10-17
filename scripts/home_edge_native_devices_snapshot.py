#!/usr/bin/env python3
"""Produce an evidence-aware, read-only Home device snapshot from the canonical registry.

The device record establishes identity; an adapter probe establishes only what it
actually measured. A missing network hint is NOT evidence that a display is off.
A host-side dependency check is NOT independent proof a peripheral is online.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
import yaml


def _decode(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _utc(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (TypeError, ValueError):
        return None


def classify(*, device_id, adapter, dependencies, online, checked_at, expires_at,
             error=None, now=None):
    """Return conservative connectivity (never infer physical power state)."""
    now = now or datetime.now(timezone.utc)
    evidence = {
        "network": "network_probe",
        "wled": "wled_api",
        "usb_hid": "usb_hid",
        "brother": "printer_adapter",
        "host": "host_controller",
    }.get(adapter, "adapter")
    base = {"physical_state": "unknown", "evidence_type": evidence}
    if _utc(expires_at) and _utc(expires_at) <= now:
        return {**base, "connectivity_status": "unknown", "status_label": "Дані застаріли"}
    if not _utc(checked_at):
        return {**base, "connectivity_status": "unknown", "status_label": "Не перевірено"}
    if error == "missing_ip_hint":
        return {**base, "connectivity_status": "unknown", "status_label": "Немає способу перевірки"}
    if adapter == "host" and device_id != "home_edge_01" and dependencies:
        return {**base, "connectivity_status": "controller_only",
                "status_label": "Контролер доступний" if online else "Стан невідомий"}
    if online is None:
        return {**base, "connectivity_status": "unknown", "status_label": "Не перевірено"}
    if adapter == "usb_hid":
        return {**base, "connectivity_status": "connected" if online else "disconnected",
                "status_label": "Підключено" if online else "Не підключено"}
    if online:
        return {**base, "connectivity_status": "reachable", "status_label": "На зв’язку"}
    if adapter in ("network", "wled"):
        return {**base, "connectivity_status": "unreachable", "status_label": "Не відповідає"}
    return {**base, "connectivity_status": "unknown", "status_label": "Стан невідомий"}


def render(database: Path, registry_path: Path, now=None):
    now = now or datetime.now(timezone.utc)
    registry = (yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}).get("devices") or {}
    with sqlite3.connect(str(database)) as db:
        rows = list(db.execute(
            "SELECT device_id,name,role,adapter,privacy FROM devices "
            "WHERE privacy='local_private' ORDER BY role,name"
        ))
        name_by_id = {d[0]: d[1] for d in rows}
        items = []
        for device_id, name, role, adapter, privacy in rows:
            state = {}
            for key in ("online", "error"):
                row = db.execute(
                    "SELECT value_json,checked_at,expires_at FROM state "
                    "WHERE device_id=? AND key=? ORDER BY checked_at DESC LIMIT 1",
                    (device_id, key),
                ).fetchone()
                if row:
                    state[key] = {"value": _decode(row[0]), "checked_at": row[1],
                                  "expires_at": row[2]}
            rec = registry.get(device_id) or {}
            deps = [str(x) for x in rec.get("dependencies", []) or []
                    if str(x) in name_by_id]
            controller = rec.get("controller") or ""
            if isinstance(controller, (dict, list)):
                controller = ""
            controller = str(controller)
            o = state.get("online", {})
            e = state.get("error", {})
            # Error values may remain in history after recovery; only consider
            # an error observed at or after the connectivity measurement.
            fresh_error = (e.get("value") if _utc(e.get("checked_at"))
                           and _utc(e.get("checked_at")) >= (_utc(o.get("checked_at")) or now)
                           else None)
            observation = classify(
                device_id=device_id, adapter=adapter, dependencies=deps,
                online=o.get("value"), checked_at=o.get("checked_at"),
                expires_at=o.get("expires_at"), error=fresh_error, now=now,
            )
            items.append({
                "device_id": device_id, "name": name, "role": role,
                "adapter": adapter, "online": o.get("value"),
                "checked_at": o.get("checked_at"), "expires_at": o.get("expires_at"),
                "location": str(rec.get("location") or ""),
                "dependencies": deps, "dependency_names": [name_by_id[x] for x in deps],
                "controller": controller,
                "controller_name": name_by_id.get(controller, controller),
                **observation,
            })
    return {"schema": "skeleton.home.native_devices.snapshot.v3",
            "items": items, "updated_at": int(now.timestamp())}


def main():
    parser = argparse.ArgumentParser()
    home = Path.home()
    parser.add_argument("--database", type=Path,
                        default=home / ".local/state/skeleton/device-registry/registry.sqlite3")
    parser.add_argument("--registry", type=Path,
                        default=home / ".config/skeleton/device-registry/confirmed.yaml")
    parser.add_argument("--output", type=Path,
                        default=home / ".local/state/skeleton-cast/native-devices.json")
    args = parser.parse_args()
    payload = render(args.database, args.registry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
                         encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"status": "ok", "items": len(payload["items"]),
                      "schema": payload["schema"]}))


if __name__ == "__main__":
    main()

"""Regression coverage for evidence-aware EXO device-status projection."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from home_edge_native_devices_snapshot import classify, render  # noqa: E402


NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)
CHECKED = (NOW - timedelta(seconds=10)).isoformat()
VALID = (NOW + timedelta(minutes=4)).isoformat()
STALE = (NOW - timedelta(minutes=2)).isoformat()


def status(adapter="network", online=True, error=None, device_id="device",
           dependencies=(), checked_at=CHECKED, expires_at=VALID):
    return classify(device_id=device_id, adapter=adapter,
                    dependencies=dependencies, online=online,
                    checked_at=checked_at, expires_at=expires_at,
                    error=error, now=NOW)


class ClassificationTests(unittest.TestCase):
    def test_unprobed_sharp_never_reported_offline(self):
        record = status(online=False, error="missing_ip_hint", device_id="sharp_tv_living_room")
        self.assertEqual(record["connectivity_status"], "unknown")
        self.assertEqual(record["physical_state"], "unknown")

    def test_mikrotik_unreachable_is_not_power_off(self):
        record = status(online=False, device_id="mikrotik_home")
        self.assertEqual(record["connectivity_status"], "unreachable")
        self.assertEqual(record["status_label"], "Не відповідає")
        self.assertEqual(record["physical_state"], "unknown")

    def test_host_proxy_is_not_soundbar_state(self):
        record = status(adapter="host", device_id="lg_sj2_soundbar",
                        dependencies=["home_edge_01"])
        self.assertEqual(record["connectivity_status"], "controller_only")
        self.assertEqual(record["physical_state"], "unknown")

    def test_host_itself_can_be_reachable(self):
        self.assertEqual(status(adapter="host", device_id="home_edge_01")["connectivity_status"], "reachable")

    def test_usb_disconnected_does_not_mean_power_off(self):
        record = status(adapter="usb_hid", online=False)
        self.assertEqual(record["connectivity_status"], "disconnected")
        self.assertEqual(record["physical_state"], "unknown")

    def test_expired_online_becomes_unknown(self):
        record = status(adapter="network", online=True, expires_at=STALE)
        self.assertEqual(record["connectivity_status"], "unknown")

    def test_missing_probes_become_unknown(self):
        self.assertEqual(status(online=None, checked_at=None)["connectivity_status"], "unknown")


class SnapshotTests(unittest.TestCase):
    def test_mixed_records_preserve_identity_and_explain_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = Path(tmp) / "registry.sqlite3"
            registry = Path(tmp) / "confirmed.yaml"
            with sqlite3.connect(dbpath) as conn:
                conn.execute("CREATE TABLE devices(device_id TEXT,name TEXT,role TEXT,adapter TEXT,privacy TEXT)")
                conn.execute("CREATE TABLE state(device_id TEXT,key TEXT,value_json TEXT,checked_at TEXT,expires_at TEXT)")
                examples = [
                    ("sharp_tv_living_room", "Sharp", "television_display", "network", False, "missing_ip_hint"),
                    ("mikrotik_home", "MikroTik", "secondary_router", "network", False, None),
                    ("lg_sj2_soundbar", "LG", "soundbar", "host", True, None),
                    ("home_edge_01", "Home Edge", "media_pc", "host", True, None),
                ]
                for ident, name, role, adapter, online, error in examples:
                    conn.execute("INSERT INTO devices VALUES(?,?,?,?,?)", (ident,name,role,adapter,"local_private"))
                    conn.execute("INSERT INTO state VALUES(?,?,?,?,?)",
                                 (ident,"online",json.dumps(online),CHECKED,VALID))
                    if error is not None:
                        conn.execute("INSERT INTO state VALUES(?,?,?,?,?)",
                                     (ident,"error",json.dumps(error),NOW.isoformat(),VALID))
            registry.write_text("devices:\n  lg_sj2_soundbar:\n    dependencies:\n      - home_edge_01\n", encoding="utf-8")
            data = render(dbpath, registry, NOW)
            by_id = {x["device_id"]: x for x in data["items"]}
            self.assertEqual(len(by_id), 4)
            self.assertEqual(by_id["sharp_tv_living_room"]["connectivity_status"], "unknown")
            self.assertEqual(by_id["mikrotik_home"]["connectivity_status"], "unreachable")
            self.assertEqual(by_id["lg_sj2_soundbar"]["connectivity_status"], "controller_only")
            self.assertEqual(by_id["home_edge_01"]["connectivity_status"], "reachable")
            self.assertEqual(by_id["lg_sj2_soundbar"]["dependency_names"], ["Home Edge"])


if __name__ == "__main__":
    unittest.main()

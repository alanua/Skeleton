from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = ROOT / "config" / "home_edge" / "home-edge-01.json"


@dataclass(frozen=True)
class HomeEdgeProfile:
    node_id: str
    hostname: str
    tailscale_ip: str
    controller_host: str
    controller_tailscale_ip: str
    target_user: str
    transport: str
    identity_env: str
    known_hosts_env: str
    task_model: str
    risk_lanes: tuple[str, ...]
    os: str
    primary_network: dict[str, Any]
    capabilities: tuple[str, ...]
    safety_boundaries: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "HomeEdgeProfile":
        ssh = _mapping(data, "ssh")
        controller = _mapping(data, "controller")
        gateway = _mapping(data, "gateway")
        primary_network = _mapping(data, "primary_network")
        profile = cls(
            node_id=_string(data, "node_id"),
            hostname=_string(data, "hostname"),
            tailscale_ip=_string(data, "tailscale_ip"),
            controller_host=_string(controller, "host"),
            controller_tailscale_ip=_string(controller, "tailscale_ip"),
            target_user=_string(ssh, "target_user"),
            transport=_string(ssh, "transport"),
            identity_env=_string(ssh, "identity_env"),
            known_hosts_env=_string(ssh, "known_hosts_env"),
            task_model=_string(gateway, "task_model"),
            risk_lanes=tuple(_strings(gateway, "risk_lanes")),
            os=_string(data, "os"),
            primary_network=dict(primary_network),
            capabilities=tuple(_strings(data, "capabilities")),
            safety_boundaries=tuple(_strings(data, "safety_boundaries")),
        )
        profile.validate_fixed_runner_contract()
        return profile

    def validate_fixed_runner_contract(self) -> None:
        expected = {
            "node_id": "home-edge-01",
            "hostname": "home-edge-01",
            "tailscale_ip": "100.127.35.74",
            "target_user": "valertos08",
            "transport": "openssh_over_tailscale_ip",
            "identity_env": "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE",
            "known_hosts_env": "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE",
            "task_model": "typed_allowlisted_actions",
        }
        for field, value in expected.items():
            if getattr(self, field) != value:
                raise ValueError(f"home-edge fixed contract mismatch for {field}")


def load_home_edge_profile(path: str | Path = DEFAULT_PROFILE_PATH) -> HomeEdgeProfile:
    profile_path = Path(path)
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("home-edge profile must be a JSON object")
    return HomeEdgeProfile.from_mapping(data)


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _strings(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{key} must be a list of non-empty strings")
    return value

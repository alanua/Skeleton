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
    user_route: str
    privileged_route: str
    os: str
    primary_network: dict[str, Any]
    capabilities: tuple[str, ...]
    safety_boundaries: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "HomeEdgeProfile":
        ssh = _mapping(data, "ssh")
        controller = _mapping(data, "controller")
        primary_network = _mapping(data, "primary_network")
        return cls(
            node_id=_string(data, "node_id"),
            hostname=_string(data, "hostname"),
            tailscale_ip=_string(data, "tailscale_ip"),
            controller_host=_string(controller, "host"),
            controller_tailscale_ip=_string(controller, "tailscale_ip"),
            target_user=_string(ssh, "target_user"),
            user_route=_string(ssh, "user_route"),
            privileged_route=_string(ssh, "privileged_route"),
            os=_string(data, "os"),
            primary_network=dict(primary_network),
            capabilities=tuple(_strings(data, "capabilities")),
            safety_boundaries=tuple(_strings(data, "safety_boundaries")),
        )


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

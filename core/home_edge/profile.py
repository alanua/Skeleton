from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = ROOT / "config" / "home_edge" / "home-edge-01.json"
LOCAL_PROFILE_ENV = "SKELETON_HOME_EDGE_01_LOCAL_PROFILE"
ENV_OVERRIDES = {
    "node_id": "SKELETON_HOME_EDGE_01_NODE_ID",
    "hostname": "SKELETON_HOME_EDGE_01_HOSTNAME",
    "tailscale_ip": "SKELETON_HOME_EDGE_01_TAILSCALE_IP",
    "controller_host": "SKELETON_HOME_EDGE_01_CONTROLLER_HOST",
    "controller_tailscale_ip": "SKELETON_HOME_EDGE_01_CONTROLLER_TAILSCALE_IP",
    "target_user": "SKELETON_HOME_EDGE_01_TARGET_USER",
    "primary_interface": "SKELETON_HOME_EDGE_01_PRIMARY_INTERFACE",
    "primary_address": "SKELETON_HOME_EDGE_01_PRIMARY_ADDRESS",
    "primary_gateway": "SKELETON_HOME_EDGE_01_PRIMARY_GATEWAY",
}


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
    strict_host_key_checking: bool
    task_model: str
    risk_lanes: tuple[str, ...]
    os: str
    primary_network: dict[str, Any]
    capabilities: tuple[str, ...]
    safety_boundaries: tuple[str, ...]

    @classmethod
    def from_mapping(
        cls,
        data: dict[str, Any],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> "HomeEdgeProfile":
        env = environment if environment is not None else os.environ
        data = _with_environment_overrides(data, env)
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
            strict_host_key_checking=bool(ssh.get("strict_host_key_checking") is True),
            task_model=_string(gateway, "task_model"),
            risk_lanes=tuple(_strings(gateway, "risk_lanes")),
            os=_string(data, "os"),
            primary_network=dict(primary_network),
            capabilities=tuple(_strings(data, "capabilities")),
            safety_boundaries=tuple(_strings(data, "safety_boundaries")),
        )
        profile.validate_contract()
        return profile

    @property
    def target(self) -> str:
        return f"{self.target_user}@{self.tailscale_ip}"

    @property
    def interactive_target(self) -> str:
        return f"{self.target_user}@{self.hostname}"

    def validate_contract(self) -> None:
        expected = {
            "transport": "openssh_over_tailscale_ip",
            "identity_env": "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE",
            "known_hosts_env": "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE",
            "task_model": "typed_allowlisted_actions",
        }
        for field, value in expected.items():
            if getattr(self, field) != value:
                raise ValueError(f"home-edge contract mismatch for {field}")
        if not self.strict_host_key_checking:
            raise ValueError("home-edge strict host key checking must remain enabled")


def load_home_edge_profile(
    path: str | Path = DEFAULT_PROFILE_PATH,
    *,
    environment: Mapping[str, str] | None = None,
) -> HomeEdgeProfile:
    env = environment if environment is not None else os.environ
    profile_path = Path(env.get(LOCAL_PROFILE_ENV, "") or path)
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("home-edge profile must be a JSON object")
    return HomeEdgeProfile.from_mapping(data, environment=env)


def _with_environment_overrides(
    data: dict[str, Any],
    environment: Mapping[str, str],
) -> dict[str, Any]:
    result = json.loads(json.dumps(data))
    for key, env_name in ENV_OVERRIDES.items():
        value = environment.get(env_name, "").strip()
        if not value:
            continue
        if key == "controller_host":
            result.setdefault("controller", {})["host"] = value
        elif key == "controller_tailscale_ip":
            result.setdefault("controller", {})["tailscale_ip"] = value
        elif key == "target_user":
            result.setdefault("ssh", {})["target_user"] = value
        elif key == "primary_interface":
            result.setdefault("primary_network", {})["interface"] = value
        elif key == "primary_address":
            result.setdefault("primary_network", {})["address"] = value
        elif key == "primary_gateway":
            result.setdefault("primary_network", {})["gateway"] = value
        else:
            result[key] = value
    return result


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

#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import subprocess
from typing import Callable


SCHEMA_VERSION = "skeleton.home_edge.screensaver_preemption.v1"
OWNER_STATUS_COMMAND = ("home-edge-media-display-owner", "status")
DEFAULT_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class ScreensaverPreemptionResult:
    show_saver: bool
    reason: str
    owner_exit_code: int | None = None

    @property
    def exit_code(self) -> int:
        return 0 if self.show_saver else 1

    def public_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "show_saver": self.show_saver,
            "reason": self.reason,
        }
        if self.owner_exit_code is not None:
            payload["owner_exit_code"] = self.owner_exit_code
        return payload


def _owner_status_is_well_formed(stdout: str, returncode: int) -> bool:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    state = payload.get("state")
    if returncode == 0:
        return state == "OWNER"
    if returncode == 1:
        return state == "CLEAR"
    if returncode == 2:
        return state == "UNKNOWN"
    return False


def decide_screensaver_preemption(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ScreensaverPreemptionResult:
    try:
        process = runner(
            list(OWNER_STATUS_COMMAND),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ScreensaverPreemptionResult(False, "owner_status_timeout")
    except (OSError, subprocess.SubprocessError):
        return ScreensaverPreemptionResult(False, "owner_status_unavailable")

    returncode = int(process.returncode)
    if not _owner_status_is_well_formed(process.stdout, returncode):
        return ScreensaverPreemptionResult(False, "owner_status_malformed", returncode)
    if returncode == 1:
        return ScreensaverPreemptionResult(True, "owner_status_clear", returncode)
    if returncode == 0:
        return ScreensaverPreemptionResult(False, "owner_status_owner", returncode)
    if returncode == 2:
        return ScreensaverPreemptionResult(False, "owner_status_unknown", returncode)
    return ScreensaverPreemptionResult(False, "owner_status_unrecognized_exit", returncode)


def public_json(result: ScreensaverPreemptionResult) -> str:
    return json.dumps(result.public_payload(), sort_keys=True, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Home Edge visual screensaver preemption gate")
    parser.add_argument("command", choices=("status",))
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()
    if args.command != "status":
        return 1
    result = decide_screensaver_preemption(timeout_seconds=args.timeout_seconds)
    print(public_json(result))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

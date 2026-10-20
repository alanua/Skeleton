"""Non-mutating local Windows workstation preflight for future DIOS pilot.

Reports only public-safe booleans. No CAD discovery, registry probing, credentials,
process injection, service creation, device identity, or private file enumeration.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import sys
from typing import Callable, Sequence


def inspect_environment(*, os_name: str | None = None,
                        python_version: tuple[int, int] | None = None,
                        dependency_present: Callable[[str], bool] | None = None
                        ) -> dict[str, object]:
    system = os_name if os_name is not None else platform.system()
    version = python_version if python_version is not None else (
        sys.version_info.major, sys.version_info.minor
    )
    checker = dependency_present or (lambda name: importlib.util.find_spec(name) is not None)
    python_supported = version >= (3, 11)
    windows_supported = system == "Windows"
    crypto_present = bool(checker("cryptography"))
    return {
        "schema": "skeleton.workstation_preflight.v1",
        "windows_supported": windows_supported,
        "python_supported": python_supported,
        "cryptography_available": crypto_present,
        "source_only_checks_passed": windows_supported and python_supported and crypto_present,
        "runtime_authorized": False,
        "installation_performed": False,
        "private_cad_adapter_verified": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only workstation capability check; never installs software."
    )
    parser.add_argument("--json", action="store_true", help="Output only safe booleans.")
    args = parser.parse_args(argv)
    result = inspect_environment()
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print("Windows preflight: " + (
            "source environment ready" if result["source_only_checks_passed"]
            else "source environment not ready"
        ))
        print("No installation or CAD integration performed.")
    return 0 if result["source_only_checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

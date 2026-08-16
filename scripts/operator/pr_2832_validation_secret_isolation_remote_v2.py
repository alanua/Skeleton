#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import urllib.request

V1_COMMIT = "824ec92265235df85d2b086dc46ace38d1488b18"
V1_URL = f"https://raw.githubusercontent.com/alanua/Skeleton/{V1_COMMIT}/scripts/operator/pr_2832_validation_secret_isolation_remote.py"
OLD = "test_path.write_text(tests.rstrip() + TEST_APPEND + '\\n', encoding='utf-8')"
NEW = "test_path.write_text(tests.rstrip() + '\\n\\n' + TEST_APPEND.strip() + '\\n', encoding='utf-8')"


def main() -> int:
    with urllib.request.urlopen(V1_URL, timeout=30) as response:
        source = response.read().decode("utf-8")
    if source.count(OLD) != 1:
        print("RESULT=BLOCKED:bootstrap_preimage_mismatch")
        return 0
    patched = source.replace(OLD, NEW, 1)
    compile(patched, "pr_2832_validation_secret_isolation_remote_v2_generated.py", "exec")
    with tempfile.TemporaryDirectory(prefix="pr2832-v2-") as tmp:
        helper = Path(tmp) / "helper.py"
        helper.write_text(patched, encoding="utf-8")
        result = subprocess.run(["python3", str(helper)], check=False)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

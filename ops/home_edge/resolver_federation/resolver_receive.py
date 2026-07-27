#!/usr/bin/env python3
from __future__ import annotations
import hashlib, os, pathlib, re, sys, tempfile

MAX = 32 * 1024 * 1024
NAME = re.compile(r"^resolver-[A-Za-z0-9_.-]+-[0-9]{8}T[0-9]{6}Z\.jsonl\.gz$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")


def main() -> int:
    if len(sys.argv) != 3 or not NAME.fullmatch(sys.argv[1]) or not DIGEST.fullmatch(sys.argv[2]):
        print("invalid resolver bundle request", file=sys.stderr)
        return 2
    root = pathlib.Path(os.environ.get("SKELETON_RESOLVER_STATE", "/var/lib/skeleton-resolver"))
    inbox = root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    data = sys.stdin.buffer.read(MAX + 1)
    if len(data) > MAX:
        print("resolver bundle too large", file=sys.stderr)
        return 2
    actual = hashlib.sha256(data).hexdigest()
    if actual != sys.argv[2]:
        print("resolver bundle checksum mismatch", file=sys.stderr)
        return 2
    target = inbox / sys.argv[1]
    fd, tmp_name = tempfile.mkstemp(prefix=".resolver-", dir=inbox)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    finally:
        pathlib.Path(tmp_name).unlink(missing_ok=True)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from core.family_document_intake import FamilyDocumentIntake, FamilyDocumentIntakeConfig


class FamilyDocumentRuntimeError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@contextmanager
def single_instance_lock(runtime_root: str | Path) -> Iterator[Path]:
    root = Path(runtime_root).expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = root / "family_document_worker.lock"
    with lock_path.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FamilyDocumentRuntimeError("worker_already_running", "family document worker is already running") from exc
        handle.write(str(os.getpid()))
        handle.flush()
        yield lock_path


class FamilyDocumentWorker:
    def __init__(
        self,
        config: FamilyDocumentIntakeConfig,
        intake: FamilyDocumentIntake,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 0.25,
    ) -> None:
        self.config = config
        self.intake = intake
        self.max_attempts = max_attempts if max_attempts != 3 else config.max_attempts
        self.backoff_seconds = backoff_seconds if backoff_seconds != 0.25 else config.backoff_seconds

    @classmethod
    def from_config_file(cls, path: str | Path, gateway: object) -> "FamilyDocumentWorker":
        config = FamilyDocumentIntakeConfig.from_mapping(json.loads(Path(path).read_text(encoding="utf-8")))
        return cls(config, FamilyDocumentIntake(config, gateway))  # type: ignore[arg-type]

    def run_once(self) -> dict[str, object]:
        with single_instance_lock(self.config.runtime_root):
            last_error: Exception | None = None
            attempts = max(1, self.max_attempts)
            for attempt in range(1, attempts + 1):
                try:
                    receipt = self.intake.process_one()
                    return receipt or {
                        "schema": "skeleton.family_document_worker_receipt.v1",
                        "status": "IDLE",
                        "privacy": "aggregate_only",
                        "aggregate_counts": {"documents_seen": 0},
                    }
                except Exception as exc:
                    last_error = exc
                    self.intake.journal.append(
                        {
                            "stage": "WORKER_RETRY",
                            "attempt": attempt,
                            "error_class": type(exc).__name__,
                        }
                    )
                    if attempt < attempts:
                        time.sleep(max(0.0, self.backoff_seconds) * attempt)
            return {
                "schema": "skeleton.family_document_worker_receipt.v1",
                "status": "ERROR",
                "error_class": type(last_error).__name__ if last_error is not None else "RuntimeError",
                "privacy": "aggregate_only",
                "aggregate_counts": {"attempts": attempts},
            }

    def run_forever(self, *, poll_seconds: float = 5.0, stop_after: int | None = None) -> None:
        iterations = 0
        while stop_after is None or iterations < stop_after:
            self.run_once()
            iterations += 1
            time.sleep(max(0.1, poll_seconds))

"""Bounded AES-GCM receipt outbox; no credentials or private CAD payload persistence.

This is a local persistence primitive, not a deployed service. Caller supplies the
machine key through registered SecretStore and authenticates acknowledgements.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import sqlite3

from core.workstation_agent import CommandReceipt, WorkstationRejected

_SCHEMA = "skeleton.workstation_receipt.v1"


class EncryptedReceiptStore:
    def __init__(self, *, path: Path, key: bytes,
                 verify_ack: Callable[[str, bytes], bool] | None,
                 max_items: int = 1000) -> None:
        if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink():
            raise WorkstationRejected("INVALID_RECEIPT_PATH")
        if not path.parent.is_dir() or path.parent.is_symlink():
            raise WorkstationRejected("INVALID_RECEIPT_DIRECTORY")
        if not isinstance(key, bytes) or len(key) != 32:
            raise WorkstationRejected("INVALID_RECEIPT_KEY")
        if verify_ack is not None and not callable(verify_ack):
            raise WorkstationRejected("INVALID_ACK_VERIFIER")
        if type(max_items) is not int or not 1 <= max_items <= 10000:
            raise WorkstationRejected("INVALID_RECEIPT_LIMIT")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            self._cipher = AESGCM(key)
        except Exception as exc:
            raise WorkstationRejected("ENCRYPTION_UNAVAILABLE") from exc
        self._verify_ack = verify_ack
        self._max_items = max_items
        if not path.exists():
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        if path.is_symlink() or not path.is_file():
            raise WorkstationRejected("INVALID_RECEIPT_FILE")
        # POSIX cannot accept publicly readable files. Windows ACLs are a private
        # installer responsibility and must be verified before enabling runtime.
        if os.name == "posix" and (path.stat().st_mode & 0o077):
            raise WorkstationRejected("INSECURE_RECEIPT_PERMISSIONS")
        self._db = sqlite3.connect(str(path), timeout=5)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA journal_mode=DELETE")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS receipts ("
            "hash TEXT PRIMARY KEY, nonce BLOB NOT NULL, encrypted BLOB NOT NULL)"
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "EncryptedReceiptStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0])

    def enqueue(self, receipt: CommandReceipt) -> bool:
        if not isinstance(receipt, CommandReceipt):
            raise WorkstationRejected("TYPED_RECEIPT_REQUIRED")
        public = receipt.public()
        if public.get("schema") != _SCHEMA:
            raise WorkstationRejected("INVALID_RECEIPT_SCHEMA")
        receipt_hash = receipt.receipt_hash
        if not isinstance(receipt_hash, str) or len(receipt_hash) != 64:
            raise WorkstationRejected("INVALID_RECEIPT_HASH")
        if self._db.execute("SELECT 1 FROM receipts WHERE hash=?", (receipt_hash,)).fetchone():
            return False
        if self.count() >= self._max_items:
            raise WorkstationRejected("RECEIPT_QUEUE_FULL")
        nonce = os.urandom(12)
        plaintext = json.dumps(public, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(plaintext) > 2048:
            raise WorkstationRejected("RECEIPT_TOO_LARGE")
        encrypted = self._cipher.encrypt(nonce, plaintext, receipt_hash.encode("ascii"))
        with self._db:
            self._db.execute("INSERT INTO receipts(hash,nonce,encrypted) VALUES (?,?,?)",
                             (receipt_hash, nonce, encrypted))
        return True

    def list_pending(self, limit: int = 100) -> tuple[dict[str, object], ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise WorkstationRejected("INVALID_LIST_LIMIT")
        rows = self._db.execute(
            "SELECT hash,nonce,encrypted FROM receipts ORDER BY rowid LIMIT ?", (limit,)
        ).fetchall()
        result: list[dict[str, object]] = []
        for digest, nonce, encrypted in rows:
            try:
                data = json.loads(self._cipher.decrypt(
                    bytes(nonce), bytes(encrypted), str(digest).encode("ascii")))
            except Exception:
                raise WorkstationRejected("RECEIPT_DECRYPT_FAILED") from None
            if data.get("schema") != _SCHEMA or data.get("receipt_hash") != digest:
                raise WorkstationRejected("RECEIPT_INTEGRITY_FAILED")
            result.append(data)
        return tuple(result)

    def acknowledge(self, receipt_hash: str, signed_ack: bytes) -> bool:
        if not isinstance(receipt_hash, str) or len(receipt_hash) != 64:
            raise WorkstationRejected("INVALID_RECEIPT_HASH")
        if not isinstance(signed_ack, bytes) or not signed_ack or self._verify_ack is None:
            raise WorkstationRejected("AUTHENTICATED_ACK_REQUIRED")
        try:
            allowed = self._verify_ack(receipt_hash, signed_ack)
        except Exception:
            allowed = False
        if allowed is not True:
            raise WorkstationRejected("AUTHENTICATED_ACK_DENIED")
        with self._db:
            cursor = self._db.execute("DELETE FROM receipts WHERE hash=?", (receipt_hash,))
        return cursor.rowcount > 0

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


TELEGRAM_STORE_SCHEMA = "skeleton.telegram_gateway.store.v1"


@dataclass(frozen=True)
class TelegramStore:
    path: Path

    @classmethod
    def open(cls, path: str | Path) -> "TelegramStore":
        store = cls(Path(path))
        store._init()
        return store

    def upsert_messages(self, messages: Iterable[Mapping[str, object]]) -> int:
        count = 0
        with self._connect() as conn:
            for message in messages:
                conn.execute(
                    """
                    INSERT INTO telegram_messages (
                        source_id, peer_id, message_id, text, sent_at, edited_at, deleted_at, media_kind, media_downloaded
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, peer_id, message_id) DO UPDATE SET
                        text=excluded.text,
                        sent_at=COALESCE(excluded.sent_at, telegram_messages.sent_at),
                        edited_at=excluded.edited_at,
                        deleted_at=excluded.deleted_at,
                        media_kind=excluded.media_kind,
                        media_downloaded=excluded.media_downloaded
                    """,
                    _row(message),
                )
                count += 1
            self._rebuild_fts(conn)
        return count

    def tombstone(self, *, source_id: str, peer_id: str, message_id: int, deleted_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO telegram_messages (source_id, peer_id, message_id, text, deleted_at)
                VALUES (?, ?, ?, '', ?)
                ON CONFLICT(source_id, peer_id, message_id) DO UPDATE SET deleted_at=excluded.deleted_at
                """,
                (source_id, peer_id, message_id, deleted_at),
            )
            self._rebuild_fts(conn)

    def latest_message_id(self, *, source_id: str, peer_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(message_id), 0) FROM telegram_messages WHERE source_id=? AND peer_id=?",
                (source_id, peer_id),
            ).fetchone()
        return int(row[0])

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, object]]:
        safe_limit = max(1, min(int(limit), 100))
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT m.source_id, m.peer_id, m.message_id, m.text, m.sent_at, m.edited_at, m.deleted_at,
                           m.media_kind, m.media_downloaded
                    FROM telegram_messages_fts f
                    JOIN telegram_messages m ON m.rowid = f.rowid
                    WHERE telegram_messages_fts MATCH ? AND m.deleted_at IS NULL
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (query, safe_limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """
                    SELECT source_id, peer_id, message_id, text, sent_at, edited_at, deleted_at, media_kind, media_downloaded
                    FROM telegram_messages
                    WHERE text LIKE ? AND deleted_at IS NULL
                    ORDER BY message_id DESC
                    LIMIT ?
                    """,
                    (f"%{query}%", safe_limit),
                ).fetchall()
        return [_dict(row) for row in rows]

    def _init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_messages (
                    source_id TEXT NOT NULL,
                    peer_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    sent_at TEXT,
                    edited_at TEXT,
                    deleted_at TEXT,
                    media_kind TEXT,
                    media_downloaded INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (source_id, peer_id, message_id)
                )
                """
            )
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS telegram_messages_fts USING fts5(text, content='telegram_messages', content_rowid='rowid')"
                )
            except sqlite3.OperationalError:
                pass
            self._rebuild_fts(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _rebuild_fts(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("INSERT INTO telegram_messages_fts(telegram_messages_fts) VALUES ('rebuild')")
        except sqlite3.DatabaseError:
            return


def _row(message: Mapping[str, object]) -> tuple[object, ...]:
    media = message.get("media")
    media_kind = media.get("kind") if isinstance(media, Mapping) else None
    media_downloaded = bool(media.get("downloaded")) if isinstance(media, Mapping) else False
    return (
        message["source_id"],
        message["peer_id"],
        int(message["message_id"]),
        str(message.get("text") or ""),
        message.get("sent_at"),
        message.get("edited_at"),
        message.get("deleted_at"),
        media_kind,
        int(media_downloaded),
    )


def _dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "source_id": row["source_id"],
        "peer_id": row["peer_id"],
        "message_id": row["message_id"],
        "text": row["text"],
        "sent_at": row["sent_at"],
        "edited_at": row["edited_at"],
        "deleted_at": row["deleted_at"],
        "media": {"kind": row["media_kind"], "downloaded": bool(row["media_downloaded"])} if row["media_kind"] else None,
    }

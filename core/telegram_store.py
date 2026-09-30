from __future__ import annotations

import json
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
                        source_id, peer_id, message_id, peer_channel_id, username, title, text, sent_at, edited_at,
                        deleted_at, sender_id, sender_username, sender_name, author, entities_json, urls_json,
                        reply_to_message_id, thread_id, forward_json, media_kind, media_downloaded, media_caption,
                        media_json, permalink, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, peer_id, message_id) DO UPDATE SET
                        peer_channel_id=COALESCE(excluded.peer_channel_id, telegram_messages.peer_channel_id),
                        username=COALESCE(excluded.username, telegram_messages.username),
                        title=COALESCE(excluded.title, telegram_messages.title),
                        text=excluded.text,
                        sent_at=COALESCE(excluded.sent_at, telegram_messages.sent_at),
                        edited_at=excluded.edited_at,
                        deleted_at=excluded.deleted_at,
                        sender_id=excluded.sender_id,
                        sender_username=excluded.sender_username,
                        sender_name=excluded.sender_name,
                        author=excluded.author,
                        entities_json=excluded.entities_json,
                        urls_json=excluded.urls_json,
                        reply_to_message_id=excluded.reply_to_message_id,
                        thread_id=excluded.thread_id,
                        forward_json=excluded.forward_json,
                        media_kind=excluded.media_kind,
                        media_downloaded=excluded.media_downloaded,
                        media_caption=excluded.media_caption,
                        media_json=excluded.media_json,
                        permalink=COALESCE(excluded.permalink, telegram_messages.permalink),
                        raw_json=excluded.raw_json
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

    def apply_sync_batch(
        self,
        *,
        source_id: str,
        peer_id: str,
        messages: Iterable[Mapping[str, object]],
        deleted_message_ids: Iterable[int] = (),
        deleted_at: str,
        cursor_message_id: int,
        updated_at: str,
        state: Mapping[str, object] | None = None,
    ) -> int:
        stored = 0
        with self._connect() as conn:
            for message in messages:
                conn.execute(
                    """
                    INSERT INTO telegram_messages (
                        source_id, peer_id, message_id, peer_channel_id, username, title, text, sent_at, edited_at,
                        deleted_at, sender_id, sender_username, sender_name, author, entities_json, urls_json,
                        reply_to_message_id, thread_id, forward_json, media_kind, media_downloaded, media_caption,
                        media_json, permalink, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, peer_id, message_id) DO UPDATE SET
                        peer_channel_id=COALESCE(excluded.peer_channel_id, telegram_messages.peer_channel_id),
                        username=COALESCE(excluded.username, telegram_messages.username),
                        title=COALESCE(excluded.title, telegram_messages.title),
                        text=excluded.text,
                        sent_at=COALESCE(excluded.sent_at, telegram_messages.sent_at),
                        edited_at=excluded.edited_at,
                        deleted_at=excluded.deleted_at,
                        sender_id=excluded.sender_id,
                        sender_username=excluded.sender_username,
                        sender_name=excluded.sender_name,
                        author=excluded.author,
                        entities_json=excluded.entities_json,
                        urls_json=excluded.urls_json,
                        reply_to_message_id=excluded.reply_to_message_id,
                        thread_id=excluded.thread_id,
                        forward_json=excluded.forward_json,
                        media_kind=excluded.media_kind,
                        media_downloaded=excluded.media_downloaded,
                        media_caption=excluded.media_caption,
                        media_json=excluded.media_json,
                        permalink=COALESCE(excluded.permalink, telegram_messages.permalink),
                        raw_json=excluded.raw_json
                    """,
                    _row(message),
                )
                stored += 1
            for message_id in deleted_message_ids:
                conn.execute(
                    """
                    INSERT INTO telegram_messages (source_id, peer_id, message_id, text, deleted_at)
                    VALUES (?, ?, ?, '', ?)
                    ON CONFLICT(source_id, peer_id, message_id) DO UPDATE SET deleted_at=excluded.deleted_at
                    """,
                    (source_id, peer_id, int(message_id), deleted_at),
                )
            conn.execute(
                """
                INSERT INTO telegram_sync_cursors (source_id, peer_id, cursor_message_id, updated_at, state_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, peer_id) DO UPDATE SET
                    cursor_message_id=excluded.cursor_message_id,
                    updated_at=excluded.updated_at,
                    state_json=excluded.state_json
                """,
                (source_id, peer_id, int(cursor_message_id), updated_at, _json(state or {})),
            )
            self._rebuild_fts(conn)
        return stored

    def latest_message_id(self, *, source_id: str, peer_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(message_id), 0) FROM telegram_messages WHERE source_id=? AND peer_id=?",
                (source_id, peer_id),
            ).fetchone()
        return int(row[0])

    def get_message(self, *, source_id: str, peer_id: str, message_id: int) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(f"{_SELECT_MESSAGES} WHERE source_id=? AND peer_id=? AND message_id=?", (source_id, peer_id, message_id)).fetchone()
        return None if row is None else _dict(row)

    def get_history(
        self,
        *,
        source_id: str,
        peer_id: str,
        limit: int = 20,
        offset_id: int = 0,
        min_date: str | None = None,
        max_date: str | None = None,
    ) -> list[dict[str, object]]:
        safe_limit = max(1, min(int(limit), 100))
        predicates = ["source_id=?", "peer_id=?", "deleted_at IS NULL"]
        params: list[object] = [source_id, peer_id]
        if offset_id:
            predicates.append("message_id < ?")
            params.append(int(offset_id))
        if min_date:
            predicates.append("sent_at >= ?")
            params.append(min_date)
        if max_date:
            predicates.append("sent_at <= ?")
            params.append(max_date)
        params.append(safe_limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"{_SELECT_MESSAGES} WHERE {' AND '.join(predicates)} ORDER BY message_id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_dict(row) for row in rows]

    def save_sync_cursor(self, *, source_id: str, peer_id: str, cursor_message_id: int, updated_at: str, state: Mapping[str, object] | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO telegram_sync_cursors (source_id, peer_id, cursor_message_id, updated_at, state_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, peer_id) DO UPDATE SET
                    cursor_message_id=excluded.cursor_message_id,
                    updated_at=excluded.updated_at,
                    state_json=excluded.state_json
                """,
                (source_id, peer_id, int(cursor_message_id), updated_at, _json(state or {})),
            )

    def get_sync_cursor(self, *, source_id: str, peer_id: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT source_id, peer_id, cursor_message_id, updated_at, state_json FROM telegram_sync_cursors WHERE source_id=? AND peer_id=?",
                (source_id, peer_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "source_id": row["source_id"],
            "peer_id": row["peer_id"],
            "cursor_message_id": row["cursor_message_id"],
            "updated_at": row["updated_at"],
            "state": _loads(row["state_json"]),
        }

    def register_watch(self, *, source_id: str, peer_id: str, min_message_id: int = 0) -> dict[str, object]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO telegram_watches (source_id, peer_id, min_message_id, active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(source_id, peer_id) DO UPDATE SET min_message_id=excluded.min_message_id, active=1
                """,
                (source_id, peer_id, int(min_message_id)),
            )
        return {"source_id": source_id, "peer_id": peer_id, "min_message_id": int(min_message_id), "active": True}

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        source_id: str | None = None,
        peer_id: str | None = None,
        min_date: str | None = None,
        max_date: str | None = None,
    ) -> list[dict[str, object]]:
        safe_limit = max(1, min(int(limit), 100))
        filters, params = _filters(source_id=source_id, peer_id=peer_id, min_date=min_date, max_date=max_date)
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    f"""
                    SELECT {_MESSAGE_COLUMNS_PREFIXED}
                    FROM telegram_messages_fts f
                    JOIN telegram_messages m ON m.rowid = f.rowid
                    WHERE telegram_messages_fts MATCH ? AND m.deleted_at IS NULL {filters.replace('source_id', 'm.source_id').replace('peer_id', 'm.peer_id').replace('sent_at', 'm.sent_at')}
                    ORDER BY rank
                    LIMIT ?
                    """,
                    [query, *params, safe_limit],
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    f"""
                    {_SELECT_MESSAGES}
                    WHERE (text LIKE ? OR media_caption LIKE ?) AND deleted_at IS NULL {filters}
                    ORDER BY message_id DESC
                    LIMIT ?
                    """,
                    [f"%{query}%", f"%{query}%", *params, safe_limit],
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
                    peer_channel_id TEXT,
                    username TEXT,
                    title TEXT,
                    text TEXT NOT NULL DEFAULT '',
                    sent_at TEXT,
                    edited_at TEXT,
                    deleted_at TEXT,
                    sender_id TEXT,
                    sender_username TEXT,
                    sender_name TEXT,
                    author TEXT,
                    entities_json TEXT,
                    urls_json TEXT,
                    reply_to_message_id INTEGER,
                    thread_id INTEGER,
                    forward_json TEXT,
                    media_kind TEXT,
                    media_downloaded INTEGER NOT NULL DEFAULT 0,
                    media_caption TEXT,
                    media_json TEXT,
                    permalink TEXT,
                    raw_json TEXT,
                    PRIMARY KEY (source_id, peer_id, message_id)
                )
                """
            )
            self._migrate_messages(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_sync_cursors (
                    source_id TEXT NOT NULL,
                    peer_id TEXT NOT NULL,
                    cursor_message_id INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    state_json TEXT,
                    PRIMARY KEY (source_id, peer_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_watches (
                    source_id TEXT NOT NULL,
                    peer_id TEXT NOT NULL,
                    min_message_id INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (source_id, peer_id)
                )
                """
            )
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS telegram_messages_fts USING fts5(text, media_caption, content='telegram_messages', content_rowid='rowid')"
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

    def _migrate_messages(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(telegram_messages)").fetchall()}
        for name, ddl in _MESSAGE_MIGRATIONS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE telegram_messages ADD COLUMN {name} {ddl}")


def _row(message: Mapping[str, object]) -> tuple[object, ...]:
    media = message.get("media")
    media_kind = media.get("kind") if isinstance(media, Mapping) else None
    media_downloaded = bool(media.get("downloaded")) if isinstance(media, Mapping) else False
    media_caption = media.get("caption") if isinstance(media, Mapping) else None
    return (
        message["source_id"],
        message["peer_id"],
        int(message["message_id"]),
        message.get("peer_channel_id"),
        message.get("username"),
        message.get("title"),
        str(message.get("text") or ""),
        message.get("sent_at"),
        message.get("edited_at"),
        message.get("deleted_at"),
        message.get("sender_id"),
        message.get("sender_username"),
        message.get("sender_name"),
        message.get("author"),
        _json(message.get("entities", [])),
        _json(message.get("urls", [])),
        _int_or_none(message.get("reply_to_message_id")),
        _int_or_none(message.get("thread_id")),
        _json(message.get("forwarded_from")),
        media_kind,
        int(media_downloaded),
        media_caption,
        _json(media) if isinstance(media, Mapping) else None,
        message.get("permalink"),
        _json(message.get("raw", {})),
    )


def _dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "source_id": row["source_id"],
        "peer_id": row["peer_id"],
        "message_id": row["message_id"],
        "peer_channel_id": row["peer_channel_id"],
        "username": row["username"],
        "title": row["title"],
        "text": row["text"],
        "sent_at": row["sent_at"],
        "edited_at": row["edited_at"],
        "deleted_at": row["deleted_at"],
        "sender": {
            "id": row["sender_id"],
            "username": row["sender_username"],
            "name": row["sender_name"],
        }
        if row["sender_id"] or row["sender_username"] or row["sender_name"]
        else None,
        "author": row["author"],
        "entities": _loads(row["entities_json"], []),
        "urls": _loads(row["urls_json"], []),
        "reply_to_message_id": row["reply_to_message_id"],
        "thread_id": row["thread_id"],
        "forwarded_from": _loads(row["forward_json"]) if row["forward_json"] else None,
        "media": _media(row),
        "permalink": row["permalink"],
        "raw": _loads(row["raw_json"], {}),
    }


_MESSAGE_COLUMNS = (
    "source_id, peer_id, message_id, peer_channel_id, username, title, text, sent_at, edited_at, deleted_at, "
    "sender_id, sender_username, sender_name, author, entities_json, urls_json, reply_to_message_id, thread_id, "
    "forward_json, media_kind, media_downloaded, media_caption, media_json, permalink, raw_json"
)
_MESSAGE_COLUMNS_PREFIXED = ", ".join(f"m.{name.strip()}" for name in _MESSAGE_COLUMNS.split(","))
_SELECT_MESSAGES = f"SELECT {_MESSAGE_COLUMNS} FROM telegram_messages"
_MESSAGE_MIGRATIONS = {
    "peer_channel_id": "TEXT",
    "username": "TEXT",
    "title": "TEXT",
    "sender_id": "TEXT",
    "sender_username": "TEXT",
    "sender_name": "TEXT",
    "author": "TEXT",
    "entities_json": "TEXT",
    "urls_json": "TEXT",
    "reply_to_message_id": "INTEGER",
    "thread_id": "INTEGER",
    "forward_json": "TEXT",
    "media_caption": "TEXT",
    "media_json": "TEXT",
    "permalink": "TEXT",
    "raw_json": "TEXT",
}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def _loads(value: object, default: object | None = None) -> object:
    if not value:
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _media(row: sqlite3.Row) -> dict[str, object] | None:
    media = _loads(row["media_json"])
    if isinstance(media, dict):
        return media
    if row["media_kind"]:
        return {"kind": row["media_kind"], "downloaded": bool(row["media_downloaded"]), "caption": row["media_caption"]}
    return None


def _filters(*, source_id: str | None, peer_id: str | None, min_date: str | None, max_date: str | None) -> tuple[str, list[object]]:
    filters = []
    params: list[object] = []
    if source_id:
        filters.append("source_id=?")
        params.append(source_id)
    if peer_id:
        filters.append("peer_id=?")
        params.append(peer_id)
    if min_date:
        filters.append("sent_at>=?")
        params.append(min_date)
    if max_date:
        filters.append("sent_at<=?")
        params.append(max_date)
    return (" AND " + " AND ".join(filters)) if filters else "", params

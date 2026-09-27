from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterable, Mapping

SOURCE_SLEEP_AS_ANDROID = "sleep_as_android"
SCHEMA_VERSION = 1


class SleepDataError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SleepSession:
    source: str
    source_record_id: str
    start_ms: int
    end_ms: int | None
    timezone_name: str | None = None
    length_minutes: float | None = None
    rating: float | None = None
    quality: float | None = None
    snore: float | None = None
    cycles: int | None = None
    noise_level: float | None = None
    len_adjust: float | None = None
    finished: bool = True
    comment: str | None = None
    event_labels: Any = None
    events: Any = None
    raw_source: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise SleepDataError("source_required")
        if not self.source_record_id.strip():
            raise SleepDataError("source_record_id_required")
        if self.start_ms <= 0:
            raise SleepDataError("start_ms_must_be_positive")
        if self.end_ms is not None and self.end_ms < self.start_ms:
            raise SleepDataError("end_before_start")
        if self.length_minutes is not None and self.length_minutes < 0:
            raise SleepDataError("negative_length")

    @property
    def effective_duration_minutes(self) -> float | None:
        if self.length_minutes is not None:
            return float(self.length_minutes)
        if self.end_ms is None:
            return None
        return (self.end_ms - self.start_ms) / 60000.0


@dataclass(frozen=True, slots=True)
class SleepTrend:
    window_days: int
    session_count: int
    average_duration_minutes: float | None
    average_rating: float | None
    average_quality: float | None
    average_snore: float | None
    latest_end_ms: int | None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_bool(value: Any, *, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    raise SleepDataError(f"invalid_boolean:{value}")


def from_sleep_as_android_record(record: Mapping[str, Any]) -> SleepSession:
    """Normalize one Sleep as Android Content Provider record.

    Field names intentionally mirror the provider's public Record.Records API.
    Large actigraphy/noise arrays are not required for the normalized session.
    """

    source_record_id = record.get("_id")
    start_ms = record.get("startTime")
    if source_record_id is None:
        raise SleepDataError("missing_provider_record_id")
    if start_ms is None:
        raise SleepDataError("missing_provider_start_time")

    end_value = record.get("toTime")
    raw_source = {
        key: value
        for key, value in record.items()
        if key not in {"recordFullData", "recordNoiseData"}
    }

    return SleepSession(
        source=SOURCE_SLEEP_AS_ANDROID,
        source_record_id=str(source_record_id),
        start_ms=int(start_ms),
        end_ms=None if end_value in (None, "") else int(end_value),
        timezone_name=record.get("timezone"),
        length_minutes=None,
        rating=_optional_float(record.get("rating")),
        quality=_optional_float(record.get("quality")),
        snore=_optional_float(record.get("snore")),
        cycles=_optional_int(record.get("cycles")),
        noise_level=_optional_float(record.get("noiseLevel")),
        len_adjust=_optional_float(record.get("lenAdjust")),
        finished=_optional_bool(record.get("finished"), default=True),
        comment=record.get("comment"),
        event_labels=record.get("eventLabels"),
        events=record.get("events"),
        raw_source=raw_source,
    )


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload(session: SleepSession) -> dict[str, Any]:
    data = asdict(session)
    data["event_labels"] = _json_or_none(session.event_labels)
    data["events"] = _json_or_none(session.events)
    data["raw_source"] = _json_or_none(session.raw_source)
    return data


def _payload_hash(session: SleepSession) -> str:
    body = json.dumps(
        _payload(session),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


class SleepStore:
    """Local-first SQLite store for normalized sleep sessions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sleep_sessions (
                    source TEXT NOT NULL,
                    source_record_id TEXT NOT NULL,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER,
                    timezone_name TEXT,
                    length_minutes REAL,
                    rating REAL,
                    quality REAL,
                    snore REAL,
                    cycles INTEGER,
                    noise_level REAL,
                    len_adjust REAL,
                    finished INTEGER NOT NULL,
                    comment TEXT,
                    event_labels_json TEXT,
                    events_json TEXT,
                    raw_source_json TEXT,
                    payload_hash TEXT NOT NULL,
                    ingested_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (source, source_record_id)
                );
                CREATE INDEX IF NOT EXISTS idx_sleep_sessions_start
                    ON sleep_sessions(start_ms DESC);
                CREATE INDEX IF NOT EXISTS idx_sleep_sessions_end
                    ON sleep_sessions(end_ms DESC);
                """
            )
            conn.execute(
                """
                INSERT INTO metadata(key, value) VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

    def ingest(self, session: SleepSession, *, now_ms: int | None = None) -> str:
        self.initialize()
        timestamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
        payload_hash = _payload_hash(session)
        data = _payload(session)

        with sqlite3.connect(self.path) as conn:
            existing = conn.execute(
                """
                SELECT payload_hash FROM sleep_sessions
                WHERE source = ? AND source_record_id = ?
                """,
                (session.source, session.source_record_id),
            ).fetchone()
            if existing is not None and existing[0] == payload_hash:
                return "unchanged"

            state = "inserted" if existing is None else "updated"
            conn.execute(
                """
                INSERT INTO sleep_sessions(
                    source, source_record_id, start_ms, end_ms, timezone_name,
                    length_minutes, rating, quality, snore, cycles, noise_level,
                    len_adjust, finished, comment, event_labels_json, events_json,
                    raw_source_json, payload_hash, ingested_at_ms, updated_at_ms
                ) VALUES(
                    :source, :source_record_id, :start_ms, :end_ms, :timezone_name,
                    :length_minutes, :rating, :quality, :snore, :cycles, :noise_level,
                    :len_adjust, :finished, :comment, :event_labels, :events,
                    :raw_source, :payload_hash, :ingested_at_ms, :updated_at_ms
                )
                ON CONFLICT(source, source_record_id) DO UPDATE SET
                    start_ms = excluded.start_ms,
                    end_ms = excluded.end_ms,
                    timezone_name = excluded.timezone_name,
                    length_minutes = excluded.length_minutes,
                    rating = excluded.rating,
                    quality = excluded.quality,
                    snore = excluded.snore,
                    cycles = excluded.cycles,
                    noise_level = excluded.noise_level,
                    len_adjust = excluded.len_adjust,
                    finished = excluded.finished,
                    comment = excluded.comment,
                    event_labels_json = excluded.event_labels_json,
                    events_json = excluded.events_json,
                    raw_source_json = excluded.raw_source_json,
                    payload_hash = excluded.payload_hash,
                    updated_at_ms = excluded.updated_at_ms
                """,
                {
                    **data,
                    "finished": int(session.finished),
                    "payload_hash": payload_hash,
                    "ingested_at_ms": timestamp,
                    "updated_at_ms": timestamp,
                },
            )
            return state

    def latest(self, *, completed_only: bool = True) -> SleepSession | None:
        self.initialize()
        where = "WHERE finished = 1 AND end_ms IS NOT NULL" if completed_only else ""
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"""
                SELECT * FROM sleep_sessions
                {where}
                ORDER BY COALESCE(end_ms, start_ms) DESC
                LIMIT 1
                """
            ).fetchone()
        return None if row is None else _row_to_session(row)

    def recent(
        self,
        *,
        since_ms: int,
        completed_only: bool = True,
    ) -> list[SleepSession]:
        self.initialize()
        finished_clause = "AND finished = 1 AND end_ms IS NOT NULL" if completed_only else ""
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT * FROM sleep_sessions
                WHERE start_ms >= ? {finished_clause}
                ORDER BY start_ms ASC
                """,
                (int(since_ms),),
            ).fetchall()
        return [_row_to_session(row) for row in rows]

    def trend(self, *, window_days: int, now_ms: int | None = None) -> SleepTrend:
        if window_days <= 0:
            raise SleepDataError("window_days_must_be_positive")
        end = int(time.time() * 1000) if now_ms is None else int(now_ms)
        sessions = self.recent(
            since_ms=end - window_days * 24 * 60 * 60 * 1000,
            completed_only=True,
        )
        return summarize_trend(sessions, window_days=window_days)


def _row_to_session(row: sqlite3.Row) -> SleepSession:
    def decode(value: str | None) -> Any:
        return None if value is None else json.loads(value)

    return SleepSession(
        source=row["source"],
        source_record_id=row["source_record_id"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        timezone_name=row["timezone_name"],
        length_minutes=row["length_minutes"],
        rating=row["rating"],
        quality=row["quality"],
        snore=row["snore"],
        cycles=row["cycles"],
        noise_level=row["noise_level"],
        len_adjust=row["len_adjust"],
        finished=bool(row["finished"]),
        comment=row["comment"],
        event_labels=decode(row["event_labels_json"]),
        events=decode(row["events_json"]),
        raw_source=decode(row["raw_source_json"]),
    )


def _average(values: Iterable[float | int | None]) -> float | None:
    cleaned = [float(value) for value in values if value is not None]
    if not cleaned:
        return None
    return sum(cleaned) / len(cleaned)


def summarize_trend(
    sessions: Iterable[SleepSession],
    *,
    window_days: int,
) -> SleepTrend:
    completed = [
        session
        for session in sessions
        if session.finished and session.end_ms is not None
    ]
    return SleepTrend(
        window_days=window_days,
        session_count=len(completed),
        average_duration_minutes=_average(
            session.effective_duration_minutes for session in completed
        ),
        average_rating=_average(session.rating for session in completed),
        average_quality=_average(session.quality for session in completed),
        average_snore=_average(session.snore for session in completed),
        latest_end_ms=max(
            (session.end_ms for session in completed if session.end_ms is not None),
            default=None,
        ),
    )


def safe_memory_summary(trend: SleepTrend) -> dict[str, Any]:
    """Return only bounded aggregate data suitable for durable cross-domain memory."""

    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema": "skeleton.sleep.summary.v1",
        "generated_at": generated_at,
        "window_days": trend.window_days,
        "session_count": trend.session_count,
        "average_duration_minutes": trend.average_duration_minutes,
        "average_rating": trend.average_rating,
        "average_quality": trend.average_quality,
        "average_snore": trend.average_snore,
        "latest_end_ms": trend.latest_end_ms,
        "contains_raw_audio": False,
        "contains_raw_events": False,
        "clinical_interpretation": False,
    }

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping, Sequence


SCHEMA = "skeleton.operator.live_state.v1"
STALE_AFTER_SECONDS = 60


@dataclass(frozen=True)
class OperatorLiveStateSection:
    section_id: str
    title_uk: str
    value_uk: str
    status: str
    detail_uk: str = ""

    def as_dict(self) -> dict[str, str]:
        data = {
            "section_id": self.section_id,
            "title_uk": self.title_uk,
            "value_uk": self.value_uk,
            "status": self.status,
        }
        if self.detail_uk:
            data["detail_uk"] = self.detail_uk
        return data


def build_operator_live_state(
    sections: Sequence[OperatorLiveStateSection],
    *,
    observed_at_epoch_seconds: int | None = None,
    now_epoch_seconds: int | None = None,
    stale_after_seconds: int = STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    now = int(now_epoch_seconds if now_epoch_seconds is not None else time.time())
    observed_at = int(observed_at_epoch_seconds if observed_at_epoch_seconds is not None else now)
    stale = now - observed_at > stale_after_seconds
    return {
        "schema": SCHEMA,
        "observed_at_epoch_seconds": observed_at,
        "stale_after_seconds": stale_after_seconds,
        "stale": stale,
        "sections": [section.as_dict() for section in sections],
    }


def build_skeleton_cast_live_state(
    providers: Mapping[str, Callable[[], Mapping[str, Any]]],
    *,
    now_epoch_seconds: int | None = None,
) -> dict[str, Any]:
    sections = [
        _section_from_provider(
            "player",
            "Плеєр",
            providers.get("player"),
            value_keys=("state", "status", "playback_state"),
        ),
        _section_from_provider(
            "mode",
            "Режим",
            providers.get("mode"),
            value_keys=("label", "profile", "active", "mode"),
        ),
        _section_from_provider(
            "volume",
            "Гучність",
            providers.get("volume"),
            value_keys=("level", "volume", "percent"),
            suffix="%",
        ),
        _section_from_provider(
            "hyperion",
            "Підсвітка",
            providers.get("hyperion"),
            value_keys=("enabled", "status", "state"),
        ),
        _section_from_provider(
            "game",
            "Ігровий ввід",
            providers.get("game"),
            value_keys=("active", "status", "state"),
        ),
    ]
    return build_operator_live_state(
        tuple(section for section in sections if section is not None),
        now_epoch_seconds=now_epoch_seconds,
    )


def _section_from_provider(
    section_id: str,
    title_uk: str,
    provider: Callable[[], Mapping[str, Any]] | None,
    *,
    value_keys: Sequence[str],
    suffix: str = "",
) -> OperatorLiveStateSection | None:
    if provider is None:
        return None
    try:
        payload = dict(provider())
    except Exception as exc:
        return OperatorLiveStateSection(
            section_id=section_id,
            title_uk=title_uk,
            value_uk="Недоступно",
            status="UNAVAILABLE",
            detail_uk=str(exc).strip()[-160:],
        )

    value = _first_value(payload, value_keys)
    if value is None:
        value_text = "Дані отримано"
    elif isinstance(value, bool):
        value_text = "Увімкнено" if value else "Вимкнено"
    else:
        value_text = f"{value}{suffix}"
    status = str(payload.get("status") or payload.get("state") or "OK").upper()
    return OperatorLiveStateSection(
        section_id=section_id,
        title_uk=title_uk,
        value_uk=value_text,
        status=status,
    )


def _first_value(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None

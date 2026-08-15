from __future__ import annotations

from core.operator_live_state import (
    SCHEMA,
    OperatorLiveStateSection,
    build_operator_live_state,
    build_skeleton_cast_live_state,
)


def test_live_state_uses_canonical_schema_and_path_ready_payload() -> None:
    payload = build_operator_live_state(
        (
            OperatorLiveStateSection(
                section_id="player",
                title_uk="Плеєр",
                value_uk="playing",
                status="OK",
            ),
        ),
        observed_at_epoch_seconds=100,
        now_epoch_seconds=110,
    )

    assert payload["schema"] == SCHEMA
    assert payload["observed_at_epoch_seconds"] == 100
    assert payload["stale"] is False
    assert payload["sections"] == [
        {
            "section_id": "player",
            "title_uk": "Плеєр",
            "value_uk": "playing",
            "status": "OK",
        }
    ]


def test_stale_live_state_is_explicit() -> None:
    payload = build_operator_live_state(
        (),
        observed_at_epoch_seconds=100,
        now_epoch_seconds=200,
        stale_after_seconds=60,
    )

    assert payload["stale"] is True


def test_skeleton_cast_projection_marks_failed_probe_unavailable_without_fake_rows() -> None:
    payload = build_skeleton_cast_live_state(
        {
            "player": lambda: {"state": "playing"},
            "mode": lambda: {"label": "Відео"},
            "volume": lambda: {"level": 31},
            "hyperion": lambda: (_ for _ in ()).throw(RuntimeError("controller offline")),
            "game": lambda: {"active": False},
        },
        now_epoch_seconds=100,
    )

    sections = {section["section_id"]: section for section in payload["sections"]}
    assert set(sections) == {"player", "mode", "volume", "hyperion", "game"}
    assert sections["volume"]["value_uk"] == "31%"
    assert sections["hyperion"]["status"] == "UNAVAILABLE"
    assert sections["hyperion"]["value_uk"] == "Недоступно"

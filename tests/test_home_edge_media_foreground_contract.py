from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from core.home_edge.media_foreground_contract import Snapshot, Status, evaluate


BASE = Snapshot(
    sampled_at=10, mode="kiosk", tty="tty1", chrome_running=True,
    youtube_backend="youtube-web", chrome_xwayland_foreground=True,
    mpv_running=False, mpv_wayland_mapped=False, mpv_fullscreen=False,
    mpv_always_on_top=False, chrome_visible_unobscured=True,
    paused=False, user_paused=False, buffering=False, playback_position=20,
    frame_counter=200, video_identity="video-a",
)
PREVIOUS = replace(BASE, sampled_at=9, playback_position=19, frame_counter=190)


def decision(**changes):
    return evaluate(replace(BASE, **changes), PREVIOUS, now=10)


def test_legacy_chrome_backend_is_accepted():
    current = replace(BASE, youtube_backend="chrome")
    previous = replace(PREVIOUS, youtube_backend="chrome")
    outcome = evaluate(current, previous, now=10)
    assert outcome.reason == "playback_advancing"
    assert outcome.foreground == "visible"


def test_canonical_kiosk_backend_with_mpv_mapped_on_top():
    outcome = decision(mpv_running=True, mpv_paused=True,
                       mpv_fullscreen=True, mpv_always_on_top=True,
                       mpv_wayland_mapped=True, chrome_visible_unobscured=False)
    assert BASE.mode == "kiosk"
    assert BASE.youtube_backend == PREVIOUS.youtube_backend == "youtube-web"
    assert outcome.reason == "mpv_overlay"
    assert outcome.foreground == "obscured"
    assert outcome.playback == "unknown"
    assert outcome.next_step == "unmap_mpv_surface_via_registered_path"


@pytest.mark.parametrize("changes,reason,foreground", [
    ({}, "playback_advancing", "visible"),  # cold recovery
    ({"mpv_running": True}, "playback_advancing", "visible"),  # safely parked
    ({"mpv_running": True, "mpv_fullscreen": True, "mpv_always_on_top": True},
     "playback_advancing", "visible"),  # explicitly unmapped
    ({"chrome_visible_unobscured": None}, "visibility_unobserved", "unknown"),
    ({"chrome_visible_unobscured": False}, "chrome_occluded", "obscured"),
    ({"mpv_wayland_mapped": True}, "visibility_conflict", "at_risk"),
    ({"chrome_running": False}, "chrome_absent", "unknown"),
    ({"chrome_running": None}, "missing_evidence", "unknown"),
    ({"tty": None}, "inconsistent_tty", "unknown"),
    ({"tty": "tty2"}, "inconsistent_tty", "unknown"),
    ({"mode": "mpv"}, "not_kiosk_youtube", "not_applicable"),
    ({"youtube_backend": "mpv"}, "not_kiosk_youtube", "not_applicable"),
    ({"youtube_backend": None}, "missing_evidence", "unknown"),
    ({"paused": True, "user_paused": True}, "user_paused", "visible"),
    ({"paused": True}, "pause_unattributed", "visible"),
    ({"buffering": True}, "buffering", "visible"),
    ({"video_identity": "video-b"}, "different_media", "visible"),
    ({"video_identity": None}, "progress_unobserved", "visible"),
    ({"frame_counter": 190}, "frozen_progress", "visible"),
    ({"frame_counter": 180}, "progress_unobserved", "visible"),
    ({"playback_position": 18}, "progress_unobserved", "visible"),
    ({"playback_position": None}, "progress_unobserved", "visible"),
    ({"playback_position": float("nan")}, "progress_unobserved", "visible"),
    ({"paused": None}, "progress_unobserved", "visible"),
    ({"chrome_xwayland_foreground": False}, "playback_advancing", "visible"),
])
def test_synthetic_variants(changes, reason, foreground):
    outcome = decision(**changes)
    assert outcome.reason == reason
    assert outcome.foreground == foreground
    assert outcome.status is Status.UNKNOWN
    assert outcome.next_step != "stop_mpv_via_guarded_existing_operation"


@pytest.mark.parametrize("mapped,visibility,reason,foreground,step", [
    (True, False, "mpv_overlay", "obscured", "unmap_mpv_surface_via_registered_path"),
    (True, None, "mpv_overlay", "obscured", "unmap_mpv_surface_via_registered_path"),
    (True, True, "visibility_conflict", "at_risk", "review_kiosk_visibility"),
    (None, None, "mpv_mapping_unknown", "unknown", "review_kiosk_visibility"),
    (None, True, "mpv_mapping_unknown", "unknown", "review_kiosk_visibility"),
])
def test_legacy_warm_overlay_even_with_focus_and_time_progress(
    mapped, visibility, reason, foreground, step,
):
    # MPV's pause state cannot make its mapped fullscreen surface safe.
    outcome = decision(mpv_running=True, mpv_paused=True,
                       mpv_fullscreen=True, mpv_always_on_top=True,
                       mpv_wayland_mapped=mapped, chrome_visible_unobscured=visibility)
    assert (outcome.reason, outcome.foreground, outcome.next_step) == (reason, foreground, step)
    assert outcome.playback == "unknown"


@pytest.mark.parametrize("timestamp,previous_time,now", [
    (10, 9, 16), (10, 10, 10), (10, 11, 11), (11, 9, 10),
    (10, 3, 10), (float("nan"), 9, 10), (10, 9, float("inf")),
])
def test_stale_reversed_and_invalid_time(timestamp, previous_time, now):
    outcome = evaluate(replace(BASE, sampled_at=timestamp),
                       replace(PREVIOUS, sampled_at=previous_time), now=now)
    assert outcome.reason == "invalid_or_stale_sample"
    assert outcome.foreground == "unknown"


@pytest.mark.parametrize("changes,status", [
    ({}, Status.UNKNOWN),
    ({"command_sent": True}, Status.SENT),
    ({"command_sent": True, "command_accepted": True}, Status.ACCEPTED),
    ({"command_sent": True, "command_accepted": True, "command_applied": True}, Status.APPLIED),
    ({"command_applied": True}, Status.UNKNOWN),
])
def test_command_levels_never_prove_physical_visibility(changes, status):
    outcome = decision(chrome_visible_unobscured=None, **changes)
    assert outcome.status is status
    assert outcome.foreground == "unknown"
    assert outcome.status is not Status.PHYSICALLY_VERIFIED


def test_pause_to_play_rpc_cannot_prove_progress():
    outcome = evaluate(replace(BASE, command_sent=True, command_accepted=True,
                               command_applied=True), replace(PREVIOUS, paused=True), now=10)
    assert outcome.status is Status.APPLIED
    assert outcome.reason == "progress_unobserved"


def test_single_sample_and_missing_tty():
    assert evaluate(BASE, now=10).reason == "progress_unobserved"
    assert evaluate(replace(BASE, tty=None), now=10).reason == "missing_evidence"


def test_no_frame_counter_allows_only_position_evidence():
    previous = replace(PREVIOUS, frame_counter=None)
    current = replace(BASE, frame_counter=None)
    assert evaluate(current, previous, now=10).playback == "advancing"
    assert evaluate(replace(current, playback_position=19), previous, now=10).playback == "frozen"


def test_frame_progress_at_same_position():
    assert decision(playback_position=19).playback == "advancing"


def test_snapshots_are_immutable():
    with pytest.raises(FrozenInstanceError):
        BASE.mode = "mpv"


def test_source_only_no_hidden_executor():
    source = Path(__file__).parents[1] / "core/home_edge/media_foreground_contract.py"
    tree = ast.parse(source.read_text())
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports == {"__future__", "dataclasses", "enum", "math", "typing"}
    assert not any(isinstance(node, (ast.Import, ast.AsyncFunctionDef)) for node in ast.walk(tree))
    # No callbacks, file/process/network access, or dynamic execution entry points.
    calls = {node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert calls <= {"dataclass", "Outcome", "result", "visible", "all", "any", "isfinite", "isinstance"}


@pytest.mark.parametrize("bounds", [
    {"max_age": 0}, {"max_interval": -1}, {"max_age": float("inf")},
])
def test_invalid_bounds(bounds):
    assert evaluate(BASE, PREVIOUS, now=10, **bounds).reason == "invalid_or_stale_sample"


def test_freshness_boundary_is_inclusive():
    assert evaluate(BASE, PREVIOUS, now=14).reason == "playback_advancing"

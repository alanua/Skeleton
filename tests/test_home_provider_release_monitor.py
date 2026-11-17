from dataclasses import replace
from datetime import date

import pytest

from core.home_edge.provider_release_monitor import (
    PREMIER_REPORTAZH as SERIES, State, acknowledge, assess_episode, capability,
    episode_key, failure_kind, history_key, monitor_domain, pending,
    resolve_series, set_enabled,
)

TODAY = date(2035, 3, 2)


def episode(**kwargs):
    args = dict(release_date="2035-03-01", today=TODAY,
                identity_verified=True, announcement_verified=True,
                verified_source_ids=("synthetic-full-episode",))
    args.update(kwargs)
    return assess_episode(SERIES, 1, 1, **args)


def test_exact_anchor_and_stable_identity():
    assert resolve_series(SERIES.canonical_url) == SERIES
    assert SERIES.media_uid == "provider:premier:tv:reportazh-2035"
    assert episode_key(SERIES, 1, 2) == SERIES.media_uid + ":s1:e2"


@pytest.mark.parametrize("url", [
    "https://premier.one.evil/show/reportazh-2035",
    "https://premier.one@evil/show/reportazh-2035",
    "https://evil@premier.one/show/reportazh-2035",
    "http://premier.one/show/reportazh-2035",
    "https://premier.one:443/show/reportazh-2035",
    "https://premier.one/show/", "https://premier.one/show/another-title",
    "https://premier.one/show/reportazh-2035?x=1",
    "https://premier.one/show/reportazh-2035#episode",
    "https://premier.one/show/%72eportazh-2035", "", None,
])
def test_spoofing_and_missing_identity_rejected(url):
    with pytest.raises(ValueError):
        resolve_series(url)


def test_checked_dates_and_distinct_states():
    assert episode(identity_verified="true").state == State.UNVERIFIED
    assert episode(identity_verified=False).state == State.UNVERIFIED
    assert episode(announcement_verified=False).state == State.UNVERIFIED
    assert episode(release_date=None).state == State.UNVERIFIED
    assert episode(verified_source_ids=()).state == State.ANNOUNCED
    assert episode(release_date="2035-03-03").state == State.ANNOUNCED
    assert not episode(release_date="2035-03-03").source_ids
    assert episode().state == State.VERIFIED
    for value in ("2035-02-30", "2035-3-1", "yesterday", ""):
        with pytest.raises(ValueError):
            episode(release_date=value)
    for sources in (("",), (" ",), (None,), "source"):
        with pytest.raises(ValueError):
            episode(verified_source_ids=sources)


def test_opt_in_off_and_independent_devices():
    with pytest.raises(ValueError, match="opt-in"):
        set_enabled(SERIES, "a", True)
    a = set_enabled(SERIES, "a", True, explicit_opt_in=True)
    b = set_enabled(SERIES, "b", True, explicit_opt_in=True)
    e = episode()
    a = acknowledge(a, e)
    assert pending(SERIES, a, (e,)) == ()
    assert pending(SERIES, b, (e, e)) == (e,)
    off = set_enabled(SERIES, "b", False, current=b)
    assert pending(SERIES, off, (e,)) == ()
    assert capability(SERIES, off) == dict(supported=True, enabled=False, reason="")
    assert not capability(None)["supported"]
    with pytest.raises(ValueError):
        set_enabled(SERIES, "b", False, current=a)
    with pytest.raises(ValueError):
        acknowledge(b, episode(verified_source_ids=()))


def test_cross_title_and_episode_collisions_rejected():
    a = set_enabled(SERIES, "a", True, explicit_opt_in=True)
    with pytest.raises(ValueError):
        pending(SERIES, a, (replace(episode(), key="provider:other:tv:x:s1:e1"),))
    with pytest.raises(ValueError):
        episode_key(replace(SERIES, provider_id="other"), 1, 1)
    for n in (0, -1, True, "1"):
        with pytest.raises(ValueError):
            episode_key(SERIES, n, 1)
    assert episode_key(SERIES, 1, 2) != episode_key(SERIES, 2, 1)


def test_one_canonical_history_row_across_devices_and_polls():
    rows = {}
    for device in ("a", "b", "a"):
        rows[history_key(resolve_series(SERIES.canonical_url))] = {"device": device}
    assert len(rows) == 1


def test_tmdb_dispatch_preserved_without_fabricating_id():
    assert monitor_domain("tv", 123, SERIES.canonical_url) == "tmdb"
    assert monitor_domain("tv", None, SERIES.canonical_url) == "provider"
    assert monitor_domain("movie", 123, SERIES.canonical_url) == "unsupported"
    assert monitor_domain("tv", 0, "") == "unsupported"
    assert monitor_domain("tv", True, None) == "unsupported"
    assert not hasattr(SERIES, "tmdb_id")


def test_conflict_and_timeout_are_not_unsupported():
    assert failure_kind(http_status=409) == "conflict"
    assert failure_kind(timed_out=True) == "timeout"
    assert failure_kind(http_status=500) == "server_error"

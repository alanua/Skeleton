"""Pure provider monitor domain functions. No IO, scheduler, or TMDB mutation.

Evidence passed here must come from the host's trusted verifier, never job/client
claims. A registry entry is an independently reviewed identity anchor, not proof
that any episode is available. The host owns persistence and transactions.
"""
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
import re
from urllib.parse import urlsplit


class State(str, Enum):
    UNVERIFIED = "unverified"
    ANNOUNCED = "announced"
    VERIFIED = "verified"


@dataclass(frozen=True)
class Series:
    provider: str
    provider_id: str
    canonical_url: str
    media_type: str = "tv"

    @property
    def media_uid(self):
        return f"provider:{self.provider}:tv:{self.provider_id}"


# Exact immutable URL anchor; do not infer identity from a title or search hit.
PREMIER_REPORTAZH = Series("premier", "reportazh-2035", "https://premier.one/show/reportazh-2035")
REGISTRY = (PREMIER_REPORTAZH,)


def resolve_series(url: str, media_type: str = "tv") -> Series:
    if not isinstance(url, str):
        raise ValueError("canonical URL required")
    parsed = urlsplit(url)
    if (media_type != "tv" or parsed.scheme != "https" or
            parsed.netloc != "premier.one" or parsed.query or parsed.fragment or
            not re.fullmatch(r"/show/[a-z0-9]+(?:-[a-z0-9]+)*", parsed.path)):
        raise ValueError("unsupported canonical TV URL")
    for series in REGISTRY:
        if url == series.canonical_url:
            return series
    raise ValueError("identity has no independently verified anchor")


def _registered(series: Series):
    if series not in REGISTRY:
        raise ValueError("unregistered identity")


def episode_key(series: Series, season: int, episode: int) -> str:
    _registered(series)
    if any(type(n) is not int or n <= 0 for n in (season, episode)):
        raise ValueError("positive season and episode required")
    return f"{series.media_uid}:s{season}:e{episode}"


@dataclass(frozen=True)
class Episode:
    key: str
    state: State
    release_date: date | None
    source_ids: tuple[str, ...] = ()


def assess_episode(series: Series, season: int, episode: int, *,
                   release_date: str | None, today: date,
                   identity_verified: bool = False,
                   announcement_verified: bool = False,
                   verified_source_ids: tuple[str, ...] = ()) -> Episode:
    """Source IDs must identify playable full episodes bound to this exact key.

    A date alone, catalogue entry, trailer, or empty source is never playable.
    The trusted verifier must check identity, episode binding and playability.
    """
    key = episode_key(series, season, episode)
    checked = None
    if release_date is not None:
        if not isinstance(release_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", release_date):
            raise ValueError("ISO release date required")
        checked = date.fromisoformat(release_date)
    if not isinstance(verified_source_ids, tuple) or any(
        not isinstance(s, str) or not s.strip() for s in verified_source_ids
    ):
        raise ValueError("nonempty verified source IDs required")
    state = State.UNVERIFIED
    sources = ()
    if identity_verified is True and announcement_verified is True and checked is not None:
        state = State.ANNOUNCED
        if checked <= today and verified_source_ids:
            state = State.VERIFIED
            sources = tuple(sorted(set(verified_source_ids)))
    return Episode(key, state, checked, sources)


@dataclass(frozen=True)
class Subscription:
    media_uid: str
    device_id: str
    enabled: bool = False
    seen: frozenset[str] = frozenset()


def capability(series: Series | None, subscription: Subscription | None = None) -> dict:
    supported = series in REGISTRY
    return {"supported": supported, "enabled": bool(
        supported and subscription and subscription.media_uid == series.media_uid
        and subscription.enabled), "reason": "" if supported else "unsupported_identity"}


def set_enabled(series: Series, device_id: str, enabled: bool, *,
                explicit_opt_in: bool = False,
                current: Subscription | None = None) -> Subscription:
    _registered(series)
    if not isinstance(device_id, str) or not device_id.strip() or type(enabled) is not bool:
        raise ValueError("device and boolean enabled required")
    if current and (current.media_uid, current.device_id) != (series.media_uid, device_id):
        raise ValueError("subscription identity mismatch")
    if enabled and explicit_opt_in is not True:
        raise ValueError("explicit opt-in required")
    return replace(current or Subscription(series.media_uid, device_id), enabled=enabled)


def pending(series: Series, subscription: Subscription, episodes: tuple[Episode, ...]) -> tuple[Episode, ...]:
    _registered(series)
    if subscription.media_uid != series.media_uid:
        raise ValueError("subscription identity mismatch")
    prefix = series.media_uid + ":s"
    if any(not e.key.startswith(prefix) for e in episodes):
        raise ValueError("cross-title episode")
    if not subscription.enabled:
        return ()
    unique = {e.key: e for e in episodes if e.state == State.VERIFIED and e.source_ids and e.key not in subscription.seen}
    return tuple(unique[k] for k in sorted(unique))


def acknowledge(subscription: Subscription, episode: Episode) -> Subscription:
    if (not subscription.enabled or episode.state != State.VERIFIED or not episode.source_ids
            or not episode.key.startswith(subscription.media_uid + ":s")):
        raise ValueError("not an enabled playable episode for this subscription")
    return replace(subscription, seen=subscription.seen | {episode.key})


def history_key(series: Series) -> str:
    """Upsert existing canonical title row by this key; never append per poll/device."""
    _registered(series)
    return series.media_uid


def failure_kind(*, http_status: int | None = None, timed_out: bool = False) -> str:
    if timed_out:
        return "timeout"
    if http_status == 409:
        return "conflict"
    return "server_error"


def monitor_domain(media_type: str, tmdb_id: int | None, canonical_url: str | None) -> str:
    """Dispatch only: the existing host TMDB branch remains entirely unchanged."""
    if media_type != "tv":
        return "unsupported"
    if type(tmdb_id) is int and tmdb_id > 0:
        return "tmdb"
    try:
        resolve_series(canonical_url, media_type)
    except ValueError:
        return "unsupported"
    return "provider"

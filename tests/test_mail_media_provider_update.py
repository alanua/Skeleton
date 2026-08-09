from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.mail_media_provider_update import (
    CanonicalMediaWorkResolution,
    InMemoryMailMediaProviderUpdateStore,
    JsonMailMediaProviderUpdateStore,
    MailMediaProviderUpdateError,
    MailMediaProviderUpdateInput,
    MediaProviderNoticeAdapter,
    ProviderLinkPattern,
    RefreshSinkResult,
    TargetedRefreshIntent,
    parse_media_provider_update_candidates,
    process_mail_media_provider_update,
)


class Resolver:
    def __init__(self, mapping: dict[tuple[str, str], CanonicalMediaWorkResolution]):
        self.mapping = mapping
        self.calls: list[tuple[str, str]] = []

    def resolve_provider_work(
        self, *, provider_adapter_ref: str, provider_work_ref: str
    ) -> CanonicalMediaWorkResolution:
        self.calls.append((provider_adapter_ref, provider_work_ref))
        return self.mapping.get(
            (provider_adapter_ref, provider_work_ref),
            CanonicalMediaWorkResolution(
                status="UNRESOLVED", reason_code="SYNTHETIC_WORK_UNKNOWN"
            ),
        )


class Sink:
    def __init__(self, status: str = "EMITTED"):
        self.status = status
        self.intents: list[TargetedRefreshIntent] = []

    def emit_targeted_refresh(self, intent: TargetedRefreshIntent) -> RefreshSinkResult:
        self.intents.append(intent)
        if self.status == "WAITING_DEPENDENCY":
            return RefreshSinkResult(
                status="WAITING_DEPENDENCY",
                reason_code="HOME_MEDIA_REFRESH_SINK_UNAVAILABLE",
            )
        return RefreshSinkResult(status="EMITTED")


def adapter(
    *,
    adapter_ref: str = "provider.synthetic.alpha",
    account_ref: str = "mail.account.alpha",
) -> MediaProviderNoticeAdapter:
    return MediaProviderNoticeAdapter(
        adapter_ref=adapter_ref,
        account_ref=account_ref,
        sender_evidence_patterns=(r"^sender-token-alpha$",),
        subject_patterns=(r"synthetic-update",),
        body_patterns=(r"catalog-change",),
        link_patterns=(
            ProviderLinkPattern(
                r"^https://synthetic-alpha.invalid/work/(?P<work_ref>[A-Za-z0-9._:-]+)$"
            ),
        ),
    )


def mail(
    *,
    mail_record_ref: str = "mail.local.001",
    account_ref: str = "mail.account.alpha",
    sender_evidence: str = "sender-token-alpha",
    subject: str = "synthetic-update",
    body: str = "catalog-change",
    links: tuple[str, ...] = ("https://synthetic-alpha.invalid/work/work.alpha",),
    received_at: int = 1000,
) -> MailMediaProviderUpdateInput:
    return MailMediaProviderUpdateInput(
        mail_record_ref=mail_record_ref,
        account_ref=account_ref,
        received_at=received_at,
        sender_evidence=sender_evidence,
        subject=subject,
        body=body,
        links=links,
    )


def resolved() -> CanonicalMediaWorkResolution:
    return CanonicalMediaWorkResolution(
        status="RESOLVED", canonical_work_ref="media.work.synthetic.alpha"
    )


def test_duplicate_synthetic_notices_emit_one_refresh_intent() -> None:
    store = InMemoryMailMediaProviderUpdateStore()
    sink = Sink()
    resolver = Resolver({("provider.synthetic.alpha", "work.alpha"): resolved()})
    value = mail(
        links=(
            "https://synthetic-alpha.invalid/work/work.alpha",
            "https://synthetic-alpha.invalid/work/work.alpha",
        )
    )

    receipt = process_mail_media_provider_update(
        value,
        adapters=(adapter(),),
        resolver=resolver,
        store=store,
        sink=sink,
        now=2000,
    )

    assert receipt.public_receipt()["refresh_intent_count"] == 1
    assert len(sink.intents) == 1
    assert sink.intents[0].to_mapping() == {
        "schema": "skeleton.home_media.targeted_refresh_intent.v1",
        "canonical_work_ref": "media.work.synthetic.alpha",
        "reason_code": "MAIL_PROVIDER_UPDATE_NOTICE",
        "candidate_kind": "REFRESH_TRIGGER_NOT_RELEASE_PROOF",
        "mutates_release_or_play_state": False,
    }


def test_idempotent_replay_emits_no_duplicate() -> None:
    store = InMemoryMailMediaProviderUpdateStore()
    sink = Sink()
    resolver = Resolver({("provider.synthetic.alpha", "work.alpha"): resolved()})
    kwargs = {
        "adapters": (adapter(),),
        "resolver": resolver,
        "store": store,
        "sink": sink,
        "now": 2000,
    }

    first = process_mail_media_provider_update(mail(), **kwargs)
    second = process_mail_media_provider_update(mail(), **kwargs)

    assert first.public_receipt()["refresh_intent_count"] == 1
    assert second.public_receipt()["refresh_intent_count"] == 0
    assert second.public_receipt()["deduped_count"] == 1
    assert len(sink.intents) == 1


def test_unrelated_mail_ignored() -> None:
    receipt = process_mail_media_provider_update(
        mail(subject="ordinary synthetic note"),
        adapters=(adapter(),),
        resolver=Resolver({("provider.synthetic.alpha", "work.alpha"): resolved()}),
        store=InMemoryMailMediaProviderUpdateStore(),
        sink=Sink(),
        now=2000,
    )

    assert receipt.public_receipt()["status"] == "IGNORED"
    assert receipt.public_receipt()["refresh_intent_count"] == 0


def test_malformed_unknown_provider_link_fails_closed() -> None:
    candidates = parse_media_provider_update_candidates(
        mail(links=("https://synthetic-alpha.invalid/work/not allowed",)),
        adapters=(adapter(),),
    )

    assert candidates == ()


def test_unresolved_canonical_work_does_not_create_media_record() -> None:
    resolver = Resolver({})
    sink = Sink()
    receipt = process_mail_media_provider_update(
        mail(),
        adapters=(adapter(),),
        resolver=resolver,
        store=InMemoryMailMediaProviderUpdateStore(),
        sink=sink,
        now=2000,
    )

    public = receipt.public_receipt()
    assert public["status"] == "IGNORED"
    assert public["reason_code"] == "SYNTHETIC_WORK_UNKNOWN"
    assert public["refresh_intent_count"] == 0
    assert sink.intents == []
    assert resolver.calls == [("provider.synthetic.alpha", "work.alpha")]


def test_mail_arrival_alone_never_sets_media_state_flags() -> None:
    receipt = process_mail_media_provider_update(
        mail(),
        adapters=(adapter(),),
        resolver=Resolver({("provider.synthetic.alpha", "work.alpha"): resolved()}),
        store=InMemoryMailMediaProviderUpdateStore(),
        sink=Sink(),
        now=2000,
    ).public_receipt()

    assert receipt["release_proof"] is False
    for field in ("released", "playable", "translated", "watched", "active"):
        assert receipt[field] is None
    assert receipt["home_media_mutations_executed"] is False


def test_sink_unavailable_returns_durable_waiting_dependency_result(
    tmp_path: Path,
) -> None:
    store = JsonMailMediaProviderUpdateStore(tmp_path / "mail-media-store.json")
    receipt = process_mail_media_provider_update(
        mail(),
        adapters=(adapter(),),
        resolver=Resolver({("provider.synthetic.alpha", "work.alpha"): resolved()}),
        store=store,
        sink=None,
        now=2000,
    ).public_receipt()

    assert receipt["status"] == "WAITING_DEPENDENCY"
    assert receipt["reason_code"] == "HOME_MEDIA_REFRESH_SINK_UNAVAILABLE"
    assert receipt["waiting_dependency_count"] == 1
    assert len(receipt["waiting_dependency_refs"]) == 1
    persisted = json.loads((tmp_path / "mail-media-store.json").read_text())
    assert list(persisted.values())[0]["status"] == "WAITING_DEPENDENCY"


def test_raw_private_fields_absent_from_public_receipt_serialization() -> None:
    private_values = {
        "mail.local.001",
        "sender-token-alpha",
        "synthetic-update",
        "catalog-change",
        "https://synthetic-alpha.invalid/work/work.alpha",
        "provider.synthetic.alpha",
        "work.alpha",
        "mail.account.alpha",
        "media.work.synthetic.alpha",
    }
    receipt = process_mail_media_provider_update(
        mail(),
        adapters=(adapter(),),
        resolver=Resolver({("provider.synthetic.alpha", "work.alpha"): resolved()}),
        store=InMemoryMailMediaProviderUpdateStore(),
        sink=Sink(),
        now=2000,
    ).public_receipt()
    rendered = json.dumps(receipt, sort_keys=True)

    assert receipt["private_fields_included"] is False
    assert receipt["provider_identifiers_included"] is False
    assert receipt["mail_body_or_url_included"] is False
    assert not any(value in rendered for value in private_values)


def test_synthetic_multi_account_provider_configs_remain_isolated() -> None:
    beta = MediaProviderNoticeAdapter(
        adapter_ref="provider.synthetic.beta",
        account_ref="mail.account.beta",
        sender_evidence_patterns=(r"^sender-token-beta$",),
        subject_patterns=(r"synthetic-update",),
        body_patterns=(r"catalog-change",),
        link_patterns=(
            ProviderLinkPattern(
                r"^https://synthetic-beta.invalid/work/(?P<work_ref>[A-Za-z0-9._:-]+)$"
            ),
        ),
    )
    resolver = Resolver(
        {
            ("provider.synthetic.alpha", "work.alpha"): resolved(),
            (
                "provider.synthetic.beta",
                "work.beta",
            ): CanonicalMediaWorkResolution(
                status="RESOLVED", canonical_work_ref="media.work.synthetic.beta"
            ),
        }
    )
    sink = Sink()
    receipt = process_mail_media_provider_update(
        mail(
            account_ref="mail.account.beta",
            sender_evidence="sender-token-beta",
            links=("https://synthetic-beta.invalid/work/work.beta",),
        ),
        adapters=(adapter(), beta),
        resolver=resolver,
        store=InMemoryMailMediaProviderUpdateStore(),
        sink=sink,
        now=2000,
    ).public_receipt()

    assert receipt["refresh_intent_count"] == 1
    assert sink.intents[0].canonical_work_ref == "media.work.synthetic.beta"
    assert resolver.calls == [("provider.synthetic.beta", "work.beta")]


def test_provider_matching_is_runtime_config_driven_and_provider_neutral() -> None:
    source = Path("core/mail_media_provider_update.py").read_text(encoding="utf-8")
    forbidden = ("gmail", "googleapiclient", "requests", "urllib.request", "sqlite3")

    assert not any(token in source.casefold() for token in forbidden)


def test_adapter_link_pattern_requires_named_work_ref_group() -> None:
    with pytest.raises(MailMediaProviderUpdateError) as exc:
        ProviderLinkPattern(r"^https://synthetic.invalid/work/([A-Za-z0-9._:-]+)$")

    assert exc.value.reason_code == "LINK_PATTERN_MISSING_WORK_REF"

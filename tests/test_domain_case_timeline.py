from __future__ import annotations

from copy import deepcopy

from core.domain_case_timeline import (
    DOMAIN_CASE_EVENT_SCHEMA,
    build_case_timeline,
    build_dashboard_case_aggregate,
)


def event(
    event_id: str,
    occurred_at: int,
    provider_area: str,
    event_type: str,
    *,
    case_ref: str = "case:synthetic-alpha",
    refs: list[dict[str, str]] | None = None,
    edges: list[dict[str, object]] | None = None,
    dependencies: list[dict[str, object]] | None = None,
    next_action: dict[str, object] | None = None,
    public_summary: str = "Synthetic event",
) -> dict[str, object]:
    return {
        "schema": DOMAIN_CASE_EVENT_SCHEMA,
        "event_id": event_id,
        "occurred_at": occurred_at,
        "provider_area": provider_area,
        "event_type": event_type,
        "case_ref": case_ref,
        "refs": refs or [],
        "edges": edges or [],
        "dependencies": dependencies or [],
        "next_action": next_action
        or {
            "state": "none",
            "action_ref": None,
            "reason": "no_next_action",
            "confidence": "explicit",
        },
        "public_summary": public_summary,
        "public_safe": True,
    }


def ref(kind: str, value: str) -> dict[str, str]:
    return {"kind": kind, "ref": value}


def edge(source: dict[str, str], target: dict[str, str], confidence: str = "explicit") -> dict[str, object]:
    return {
        "source_ref": source,
        "target_ref": target,
        "relationship": "relates_to",
        "confidence": confidence,
    }


def dependency(
    dependency_ref: dict[str, str], state: str = "satisfied", confidence: str = "explicit"
) -> dict[str, object]:
    return {
        "dependency_ref": dependency_ref,
        "state": state,
        "reason": "synthetic_dependency",
        "confidence": confidence,
    }


def test_synthetic_mail_event_follows_case_scheduler_document_and_finance_refs() -> None:
    mail = ref("mail", "mail:synthetic-alpha")
    schedule = ref("schedule", "schedule:synthetic-alpha")
    document = ref("document", "document:synthetic-alpha")
    finance = ref("finance", "finance:synthetic-alpha")
    events = [
        event(
            "evt:mail:alpha",
            10,
            "mail",
            "mail_received",
            refs=[mail],
            edges=[edge(mail, ref("case", "case:synthetic-alpha"))],
            next_action={
                "state": "waiting_dependency",
                "action_ref": schedule,
                "reason": "schedule_followup",
                "confidence": "explicit",
            },
            public_summary="Synthetic mail created a case",
        ),
        event(
            "evt:scheduler:alpha",
            20,
            "scheduler",
            "occurrence_done",
            refs=[schedule, ref("occurrence", "occurrence:synthetic-alpha")],
            dependencies=[dependency(mail)],
            public_summary="Synthetic scheduler occurrence completed",
        ),
        event(
            "evt:document:alpha",
            30,
            "documents",
            "document_linked",
            refs=[document],
            edges=[edge(schedule, document)],
            public_summary="Synthetic document linked",
        ),
        event(
            "evt:finance:alpha",
            40,
            "finance",
            "finance_ref_linked",
            refs=[finance],
            edges=[edge(document, finance)],
            next_action={
                "state": "ready",
                "action_ref": ref("schedule", "schedule:synthetic-next"),
                "reason": "ready_for_next_scheduler_tick",
                "confidence": "explicit",
            },
            public_summary="Synthetic finance ref linked",
        ),
    ]

    timeline = build_case_timeline(events, case_ref="case:synthetic-alpha")

    assert timeline["stable_event_ids"] == [
        "evt:document:alpha",
        "evt:finance:alpha",
        "evt:mail:alpha",
        "evt:scheduler:alpha",
    ]
    refs = {(item["kind"], item["ref"]) for item in timeline["refs"]}
    assert ("mail", "mail:synthetic-alpha") in refs
    assert ("schedule", "schedule:synthetic-alpha") in refs
    assert ("document", "document:synthetic-alpha") in refs
    assert ("finance", "finance:synthetic-alpha") in refs
    assert timeline["next_action"]["state"] == "ready"
    assert timeline["next_action"]["external_side_effects_allowed"] is False


def test_development_event_follows_case_to_runner_continuation_ref() -> None:
    continuation = ref("runner_continuation", "runner:continuation:synthetic-2386")
    events = [
        event(
            "evt:development:2386",
            50,
            "development",
            "runner_continuation_recorded",
            refs=[ref("development", "development:issue-2386"), continuation],
            next_action={
                "state": "ready",
                "action_ref": continuation,
                "reason": "continue_runner_work",
                "confidence": "explicit",
            },
            public_summary="Synthetic development continuation recorded",
        )
    ]

    timeline = build_case_timeline(events, case_ref="case:synthetic-alpha")

    refs = {(item["kind"], item["ref"]) for item in timeline["refs"]}
    assert ("runner_continuation", "runner:continuation:synthetic-2386") in refs
    assert timeline["next_action"]["action_ref"] == continuation


def test_replay_idempotency_preserves_one_timeline_event_per_stable_event_id() -> None:
    original = event(
        "evt:mail:alpha",
        10,
        "mail",
        "mail_received",
        refs=[ref("mail", "mail:synthetic-alpha")],
        public_summary="Synthetic mail created a case",
    )
    replay = deepcopy(original)
    replay["public_summary"] = "Synthetic replay duplicate"

    timeline = build_case_timeline([original, replay], case_ref="case:synthetic-alpha")

    assert timeline["event_count"] == 1
    assert timeline["stable_event_ids"] == ["evt:mail:alpha"]
    assert [item["event_id"] for item in timeline["timeline"]] == ["evt:mail:alpha"]


def test_uncertain_inferred_edges_cannot_authorize_side_effects() -> None:
    events = [
        event(
            "evt:mail:uncertain",
            10,
            "mail",
            "mail_received",
            refs=[ref("mail", "mail:synthetic-uncertain")],
            edges=[
                edge(
                    ref("mail", "mail:synthetic-uncertain"),
                    ref("document", "document:synthetic-uncertain"),
                    confidence="uncertain",
                )
            ],
            next_action={
                "state": "ready",
                "action_ref": ref("schedule", "schedule:synthetic-uncertain"),
                "reason": "ready_if_link_confirmed",
                "confidence": "uncertain",
            },
            public_summary="Synthetic uncertain edge observed",
        )
    ]

    timeline = build_case_timeline(events, case_ref="case:synthetic-alpha")

    assert timeline["next_action"]["state"] == "needs_operator"
    assert timeline["next_action"]["reason"] == "uncertain_edge_requires_operator"
    assert timeline["next_action"]["side_effect_authority"] == "not_allowed"
    assert timeline["authority"]["uncertain_edges_can_authorize_side_effects"] is False


def test_next_action_dependency_state_is_deterministic_and_dashboard_safe() -> None:
    schedule = ref("schedule", "schedule:synthetic-open")
    events = [
        event(
            "evt:scheduler:open",
            20,
            "scheduler",
            "occurrence_waiting",
            refs=[schedule],
            dependencies=[dependency(ref("document", "document:synthetic-open"), state="open")],
            next_action={
                "state": "ready",
                "action_ref": schedule,
                "reason": "resume_when_dependency_satisfied",
                "confidence": "explicit",
            },
            public_summary="Synthetic scheduler waiting on document",
        )
    ]

    first = build_dashboard_case_aggregate(events)
    second = build_dashboard_case_aggregate(list(reversed(events)))

    assert first == second
    assert first["schema"] == "skeleton.dashboard_case_aggregate.v1"
    assert first["cases"][0]["dependency_state"] == "open"
    assert first["cases"][0]["next_action"]["state"] == "waiting_dependency"
    assert first["cases"][0]["next_action"]["dashboard_safe"] is True
    assert first["cases"][0]["next_action"]["external_side_effects_allowed"] is False

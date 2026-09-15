from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Final


MAIL_SECURITY_ASSESSMENT_SCHEMA: Final = "skeleton.mail_security_assessment.v1"
MAIL_SECURITY_CASE_UPDATE_SCHEMA: Final = "skeleton.mail_security_case_update.v1"
MAIL_SECURITY_EVIDENCE_REQUEST_SCHEMA: Final = "skeleton.mail_security_evidence_request.v1"
MAIL_SECURITY_RESEARCH_REQUEST_SCHEMA: Final = "skeleton.mail_security_research_request.v1"

_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,252}\.[a-z]{2,63}$")


class MailSecurityCategory(str, Enum):
    ORDINARY = "ORDINARY"
    ACTIONABLE = "ACTIONABLE"
    INVOICE_PAYMENT = "INVOICE_PAYMENT"
    TECHNICAL = "TECHNICAL"
    SPAM = "SPAM"
    PHISHING = "PHISHING"
    SCAM = "SCAM"
    PSEUDO_INKASSO = "PSEUDO_INKASSO"
    IDENTITY_MISUSE_SUSPECTED = "IDENTITY_MISUSE_SUSPECTED"
    OFFICIAL_LEGAL_NOTICE = "OFFICIAL_LEGAL_NOTICE"


class MailRiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    PRIORITY = "PRIORITY"


@dataclass(frozen=True)
class MailRiskFinding:
    code: str
    category: MailSecurityCategory
    level: MailRiskLevel
    evidence_ref: str

    def to_mapping(self) -> dict[str, str]:
        return {
            "code": self.code,
            "category": self.category.value,
            "level": self.level.value,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class MailSecurityAssessment:
    assessment_ref: str
    message_hash: str
    category: MailSecurityCategory
    risk_level: MailRiskLevel
    findings: tuple[MailRiskFinding, ...]
    needs_operator: bool
    suppress_telegram: bool
    evidence_search_request: Mapping[str, Any] | None
    external_research_request: Mapping[str, Any] | None
    case_update: Mapping[str, Any] | None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": MAIL_SECURITY_ASSESSMENT_SCHEMA,
            "assessment_ref": self.assessment_ref,
            "message_hash": self.message_hash,
            "category": self.category.value,
            "risk_level": self.risk_level.value,
            "reason_codes": [finding.code for finding in self.findings],
            "findings": [finding.to_mapping() for finding in self.findings],
            "needs_operator": self.needs_operator,
            "suppress_telegram": self.suppress_telegram,
            "evidence_search_request": _thaw(self.evidence_search_request),
            "external_research_request": _thaw(self.external_research_request),
            "case_update": _thaw(self.case_update),
            "public_safe": True,
            "private_payloads_included": False,
            "external_side_effects_executed": False,
        }


def assess_mail_security(
    *,
    message_hash: str,
    case_ref: str,
    correspondence_ref: str,
    provider: str,
    policy_category: str,
    subject_hint: str,
    body_preview: str,
    metadata: Mapping[str, Any] | None = None,
) -> MailSecurityAssessment:
    """Evaluate security risk from bounded local evidence without side effects."""

    meta = _safe_metadata(metadata or {})
    text = f"{subject_hint} {body_preview}".lower()
    evidence_ref = _evidence_ref(message_hash)
    findings: list[MailRiskFinding] = []
    categories: list[MailSecurityCategory] = []

    def add(code: str, category: MailSecurityCategory, level: MailRiskLevel) -> None:
        findings.append(MailRiskFinding(code, category, level, evidence_ref))
        categories.append(category)

    if policy_category == "invoice":
        add("CLAIMED_PAYMENT_OR_INVOICE", MailSecurityCategory.INVOICE_PAYMENT, MailRiskLevel.MEDIUM)
    elif policy_category == "technical":
        add("TECHNICAL_CORRELATION_CANDIDATE", MailSecurityCategory.TECHNICAL, MailRiskLevel.LOW)
    elif policy_category in {"important", "general"} and _actionable_text(text):
        add("ACTIONABLE_MAIL_CANDIDATE", MailSecurityCategory.ACTIONABLE, MailRiskLevel.LOW)

    if _routine_spam(text):
        add("ROUTINE_SPAM_PATTERN", MailSecurityCategory.SPAM, MailRiskLevel.LOW)

    if _official_legal_notice(text):
        add("OFFICIAL_LEGAL_NOTICE_CANDIDATE", MailSecurityCategory.OFFICIAL_LEGAL_NOTICE, MailRiskLevel.PRIORITY)

    if _private_collector(text):
        add("PRIVATE_COLLECTOR_OR_INKASSO_CANDIDATE", MailSecurityCategory.PSEUDO_INKASSO, MailRiskLevel.HIGH)

    if _payment_demand(text):
        add("PAYMENT_DEMAND_LANGUAGE", MailSecurityCategory.INVOICE_PAYMENT, MailRiskLevel.MEDIUM)

    if _pressure_language(text):
        add("PRESSURE_OR_THREAT_LANGUAGE", MailSecurityCategory.SCAM, MailRiskLevel.HIGH)

    if _identity_misuse(text):
        add("IDENTITY_MISUSE_CLAIM", MailSecurityCategory.IDENTITY_MISUSE_SUSPECTED, MailRiskLevel.HIGH)

    divergence_codes = _domain_divergence_reason_codes(meta)
    for code in divergence_codes:
        add(code, MailSecurityCategory.PHISHING, MailRiskLevel.HIGH)

    if _auth_pass(meta):
        add("AUTHENTICATION_PASS_EVIDENCE_ONLY", MailSecurityCategory.ORDINARY, MailRiskLevel.LOW)

    if _own_domain_abuse_pattern(text, meta):
        add("OWN_DOMAIN_IMPERSONATION_PATTERN", MailSecurityCategory.PHISHING, MailRiskLevel.HIGH)

    evidence_request = None
    if _claimed_contract_or_order(text) or _payment_demand(text) or _identity_misuse(text):
        evidence_request = _evidence_search_request(
            case_ref=case_ref,
            correspondence_ref=correspondence_ref,
            message_hash=message_hash,
            intent="SEARCH_PRIVATE_CORRESPONDENCE_CASE_AND_DOCUMENT_HISTORY",
            reason_codes=("CLAIMED_CONTRACT_OR_PAYMENT_EVIDENCE_REQUIRED",),
        )
        if not _has_history_evidence(meta):
            if _identity_misuse(text):
                missing_evidence_category = MailSecurityCategory.IDENTITY_MISUSE_SUSPECTED
            elif _private_collector(text):
                missing_evidence_category = MailSecurityCategory.PSEUDO_INKASSO
            elif _payment_demand(text):
                missing_evidence_category = MailSecurityCategory.INVOICE_PAYMENT
            else:
                missing_evidence_category = MailSecurityCategory.ACTIONABLE
            add(
                "CLAIMED_HISTORY_EVIDENCE_NOT_AVAILABLE_LOCALLY",
                missing_evidence_category,
                MailRiskLevel.MEDIUM,
            )

    research_request = None
    if _known_risk_hook(meta):
        research_request = _bounded_research_request(
            provider=provider,
            correspondence_ref=correspondence_ref,
            message_hash=message_hash,
        )
        add("KNOWN_RISK_EVIDENCE_HOOK_AVAILABLE", MailSecurityCategory.SCAM, MailRiskLevel.MEDIUM)

    category = _primary_category(categories)
    risk_level = _max_level(finding.level for finding in findings)
    needs_operator = category in {
        MailSecurityCategory.ACTIONABLE,
        MailSecurityCategory.INVOICE_PAYMENT,
        MailSecurityCategory.TECHNICAL,
        MailSecurityCategory.PHISHING,
        MailSecurityCategory.SCAM,
        MailSecurityCategory.PSEUDO_INKASSO,
        MailSecurityCategory.IDENTITY_MISUSE_SUSPECTED,
        MailSecurityCategory.OFFICIAL_LEGAL_NOTICE,
    }
    if category == MailSecurityCategory.SPAM:
        needs_operator = False
    suppress_telegram = not needs_operator or category == MailSecurityCategory.SPAM

    case_update = None
    if category in {
        MailSecurityCategory.PHISHING,
        MailSecurityCategory.SCAM,
        MailSecurityCategory.PSEUDO_INKASSO,
        MailSecurityCategory.IDENTITY_MISUSE_SUSPECTED,
        MailSecurityCategory.OFFICIAL_LEGAL_NOTICE,
    }:
        case_update = _case_update(
            case_ref=case_ref,
            correspondence_ref=correspondence_ref,
            message_hash=message_hash,
            category=category,
            risk_level=risk_level,
            reason_codes=tuple(finding.code for finding in findings),
        )

    assessment_ref = "mailsec:" + _stable_hash(
        {
            "schema": MAIL_SECURITY_ASSESSMENT_SCHEMA,
            "message_hash": message_hash,
            "category": category.value,
            "reason_codes": [finding.code for finding in findings],
        }
    )[:24]
    return MailSecurityAssessment(
        assessment_ref=assessment_ref,
        message_hash=message_hash,
        category=category,
        risk_level=risk_level,
        findings=tuple(findings),
        needs_operator=needs_operator,
        suppress_telegram=suppress_telegram,
        evidence_search_request=evidence_request,
        external_research_request=research_request,
        case_update=case_update,
    )


def _safe_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key in (
        "sender_domain",
        "display_domain",
        "reply_to_domain",
        "contact_domains",
        "payment_domains",
        "authentication",
        "private_evidence_refs",
        "known_risk_evidence_ref",
    ):
        item = value.get(key)
        if key.endswith("_domain"):
            domain = _domain(item)
            if domain is not None:
                output[key] = domain
        elif key.endswith("_domains"):
            domains = tuple(domain for domain in (_domain(part) for part in _sequence(item)) if domain)
            if domains:
                output[key] = domains[:8]
        elif key == "authentication" and isinstance(item, Mapping):
            output[key] = {
                str(name).lower(): str(status).upper()
                for name, status in item.items()
                if str(name).lower() in {"spf", "dkim", "dmarc"}
            }
        elif key == "private_evidence_refs":
            refs = tuple(str(part) for part in _sequence(item) if _safe_ref(str(part)))
            if refs:
                output[key] = refs[:8]
        elif key == "known_risk_evidence_ref" and isinstance(item, str) and _safe_ref(item):
            output[key] = item
    return output


def _domain(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower().rstrip(".")
    if "@" in candidate:
        candidate = candidate.rsplit("@", 1)[1]
    candidate = candidate.strip("<>()[]{}'\"")
    return candidate if _DOMAIN_RE.fullmatch(candidate) else None


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return ()


def _safe_ref(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@/+,-]{0,255}", value))


def _domain_divergence_reason_codes(meta: Mapping[str, Any]) -> tuple[str, ...]:
    sender = meta.get("sender_domain")
    compare: list[tuple[str, str]] = []
    for key, code in (
        ("display_domain", "SENDER_DISPLAY_DOMAIN_DIVERGENCE"),
        ("reply_to_domain", "SENDER_REPLY_TO_DOMAIN_DIVERGENCE"),
    ):
        domain = meta.get(key)
        if isinstance(sender, str) and isinstance(domain, str):
            compare.append((domain, code))
    for key, code in (
        ("contact_domains", "SENDER_CONTACT_DOMAIN_DIVERGENCE"),
        ("payment_domains", "SENDER_PAYMENT_DOMAIN_DIVERGENCE"),
    ):
        domains = meta.get(key)
        if isinstance(sender, str) and isinstance(domains, tuple):
            compare.extend((domain, code) for domain in domains if isinstance(domain, str))
    return tuple(code for domain, code in compare if not _same_org_domain(str(sender), domain))


def _same_org_domain(left: str, right: str) -> bool:
    left_parts = left.split(".")
    right_parts = right.split(".")
    return left_parts[-2:] == right_parts[-2:]


def _auth_pass(meta: Mapping[str, Any]) -> bool:
    auth = meta.get("authentication")
    return isinstance(auth, Mapping) and any(str(auth.get(key)) == "PASS" for key in ("spf", "dkim", "dmarc"))


def _has_history_evidence(meta: Mapping[str, Any]) -> bool:
    refs = meta.get("private_evidence_refs")
    return isinstance(refs, tuple) and bool(refs)


def _known_risk_hook(meta: Mapping[str, Any]) -> bool:
    return isinstance(meta.get("known_risk_evidence_ref"), str)


def _pressure_language(text: str) -> bool:
    pressure_terms = ("urgent", "immediately", "final notice", "last warning", "within 24 hours", "sofort")
    threat_terms = ("lawsuit", "court action", "arrest", "threat", "legal action", "seizure")
    return any(term in text for term in threat_terms) or (
        any(term in text for term in pressure_terms) and _payment_demand(text)
    )


def _payment_demand(text: str) -> bool:
    return any(term in text for term in ("pay", "payment", "invoice", "rechnung", "bank transfer", "iban", "crypto", "gift card", "forderung", "mahnung"))


def _private_collector(text: str) -> bool:
    return any(term in text for term in ("inkasso", "debt collector", "collection agency", "private collector", "mahngebuehr", "mahngebühr"))


def _official_legal_notice(text: str) -> bool:
    return any(term in text for term in ("court notice", "official legal notice", "gericht", "amtsgericht", "mahngericht", "case number", "aktenzeichen", "summons"))


def _identity_misuse(text: str) -> bool:
    return any(term in text for term in ("identity misuse", "identity theft", "opened in your name", "contract in your name", "account in your name", "not my contract", "missbrauch ihrer identitaet", "missbrauch ihrer identität"))


def _claimed_contract_or_order(text: str) -> bool:
    return any(term in text for term in ("contract", "order", "subscription", "agreement", "vertrag", "bestellung", "abo"))


def _routine_spam(text: str) -> bool:
    return any(term in text for term in ("unsubscribe", "newsletter", "limited offer", "winner", "casino bonus", "marketing preference"))


def _actionable_text(text: str) -> bool:
    return any(term in text for term in ("important", "deadline", "action required", "please review", "confirm"))


def _own_domain_abuse_pattern(text: str, meta: Mapping[str, Any]) -> bool:
    sender = meta.get("sender_domain")
    reply_to = meta.get("reply_to_domain")
    return (
        isinstance(sender, str)
        and isinstance(reply_to, str)
        and "your own domain" in text
        and not _same_org_domain(sender, reply_to)
    )


def _primary_category(categories: Sequence[MailSecurityCategory]) -> MailSecurityCategory:
    priority = (
        MailSecurityCategory.OFFICIAL_LEGAL_NOTICE,
        MailSecurityCategory.IDENTITY_MISUSE_SUSPECTED,
        MailSecurityCategory.PSEUDO_INKASSO,
        MailSecurityCategory.PHISHING,
        MailSecurityCategory.SCAM,
        MailSecurityCategory.SPAM,
        MailSecurityCategory.INVOICE_PAYMENT,
        MailSecurityCategory.TECHNICAL,
        MailSecurityCategory.ACTIONABLE,
    )
    found = set(categories)
    for category in priority:
        if category in found:
            return category
    return MailSecurityCategory.ORDINARY


def _max_level(levels: Sequence[MailRiskLevel] | Any) -> MailRiskLevel:
    order = {
        MailRiskLevel.LOW: 0,
        MailRiskLevel.MEDIUM: 1,
        MailRiskLevel.HIGH: 2,
        MailRiskLevel.PRIORITY: 3,
    }
    result = MailRiskLevel.LOW
    for level in levels:
        if order[level] > order[result]:
            result = level
    return result


def _evidence_search_request(
    *,
    case_ref: str,
    correspondence_ref: str,
    message_hash: str,
    intent: str,
    reason_codes: tuple[str, ...],
) -> dict[str, Any]:
    request_ref = "mailev:" + _stable_hash(
        {"case_ref": case_ref, "correspondence_ref": correspondence_ref, "intent": intent}
    )[:24]
    return {
        "schema": MAIL_SECURITY_EVIDENCE_REQUEST_SCHEMA,
        "request_ref": request_ref,
        "intent": intent,
        "scope": "PRIVATE_LOCAL_CASE_CORRESPONDENCE_DOCUMENT_HISTORY_ONLY",
        "case_ref": case_ref,
        "correspondence_ref": correspondence_ref,
        "message_hash": message_hash,
        "reason_codes": list(reason_codes),
        "authority": "NO_MAILBOX_MUTATION_NO_WEB_BROWSER_NO_PROVIDER_ACTION",
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }


def _bounded_research_request(
    *,
    provider: str,
    correspondence_ref: str,
    message_hash: str,
) -> dict[str, Any]:
    request_ref = "mailresearch:" + _stable_hash(
        {"provider": provider, "correspondence_ref": correspondence_ref, "message_hash": message_hash}
    )[:24]
    return {
        "schema": MAIL_SECURITY_RESEARCH_REQUEST_SCHEMA,
        "request_ref": request_ref,
        "intent": "CHECK_KNOWN_RISK_EVIDENCE_REF",
        "scope": "BOUNDED_PROVIDER_NEUTRAL_EVIDENCE_LOOKUP_ONLY",
        "provider_alias": provider,
        "correspondence_ref": correspondence_ref,
        "message_hash": message_hash,
        "forbidden_authority": [
            "BROWSER_NAVIGATION",
            "LINK_CLICK",
            "PAYMENT",
            "MAIL_REPLY",
            "MAILBOX_MUTATION",
        ],
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }


def _case_update(
    *,
    case_ref: str,
    correspondence_ref: str,
    message_hash: str,
    category: MailSecurityCategory,
    risk_level: MailRiskLevel,
    reason_codes: tuple[str, ...],
) -> dict[str, Any]:
    update_ref = "mailcase:" + _stable_hash(
        {"case_ref": case_ref, "correspondence_ref": correspondence_ref, "category": category.value}
    )[:24]
    state = (
        MailSecurityCategory.IDENTITY_MISUSE_SUSPECTED.value
        if category == MailSecurityCategory.IDENTITY_MISUSE_SUSPECTED
        else category.value
    )
    return {
        "schema": MAIL_SECURITY_CASE_UPDATE_SCHEMA,
        "update_ref": update_ref,
        "case_ref": case_ref,
        "correspondence_ref": correspondence_ref,
        "message_hash": message_hash,
        "state": state,
        "risk_level": risk_level.value,
        "reason_codes": list(reason_codes),
        "evidence_refs": [_evidence_ref(message_hash)],
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }


def _evidence_ref(message_hash: str) -> str:
    return "mailmsg:" + message_hash[:24]


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(_thaw(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _thaw(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value

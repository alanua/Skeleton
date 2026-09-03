from core.email_message_local_inference import (
    build_email_message_prompt,
    validate_email_message_output,
)
from core.local_inference_adapters import InferenceValidationError, build_default_registry


def payload(subject="Your Bitwarden Verification Code", body="If this was not you, secure your account"):
    return {"from_addr":"Bitwarden <no-reply@bitwarden.eu>","to_addr":"me@example.com","subject":subject,"snippet":body,"body_text":body,"gmail_labels":["INBOX"]}


def output(**kw):
    v={"schema":"skeleton.email_message_inference.v2","route":"ACCEPT","primary_category":"security","message_kind":"incident_alert","importance":"high","is_marketing":False,"is_spam_suspected":False,"security_event":True,"technical_consequence":False,"action_required":False,"deadline":None,"summary_uk":"Код підтвердження входу Bitwarden.","important_points_uk":["Перевірити, чи вхід ініціювали ви."],"case_key":"bitwarden/account-access","confidence":0.96,"evidence":["Verification Code","If this was not you"],"reason_codes":[]}
    v.update(kw); return v


def test_registry_contains_email_adapter():
    assert "email_message.classify" in build_default_registry().request_types()


def test_prompt_explicitly_ignores_footer_keyword_noise():
    p=build_email_message_prompt(payload("50% discount", "Newsletter footer: security login privacy unsubscribe"))
    assert "actual intent" in p
    assert "legal footers" in p
    assert "Travel price alerts belong to travel" in p


def test_accepts_real_security_event():
    assert validate_email_message_output(output(), payload())["primary_category"] == "security"


def test_rejects_security_category_from_non_security_noise():
    bad=output(security_event=False)
    try: validate_email_message_output(bad,payload())
    except InferenceValidationError as e: assert str(e)=="security_without_security_event"
    else: raise AssertionError("expected validation failure")


def test_ads_requires_marketing_signal():
    bad=output(primary_category="ads",security_event=False,is_marketing=False)
    try: validate_email_message_output(bad,payload("Sale", "Newsletter"))
    except InferenceValidationError as e: assert str(e)=="ads_without_marketing_signal"
    else: raise AssertionError("expected validation failure")


def test_low_confidence_accept_fails_closed():
    bad=output(confidence=0.5)
    try: validate_email_message_output(bad,payload())
    except InferenceValidationError as e: assert str(e)=="acceptance_confidence_too_low"
    else: raise AssertionError("expected validation failure")


def test_prompt_separates_domain_from_message_kind_and_github_discussion():
    p=build_email_message_prompt(payload("Re: [alanua/Skeleton] P0 host-level gateway self-audit outside Codex sandbox", "shleder commented: Codex CLI sandbox posture; proposing Vetto as an approach"))
    assert "independent axes" in p
    assert "NEVER automated_report" in p
    assert "issue_discussion" in p


def test_v2_requires_message_kind():
    bad=output(); bad.pop("message_kind")
    try: validate_email_message_output(bad,payload())
    except InferenceValidationError: pass
    else: raise AssertionError("expected validation failure")


def test_accepts_technical_issue_discussion_kind():
    v=output(primary_category="technical",message_kind="issue_discussion",security_event=False,importance="normal",summary_uk="Обговорення технічної пропозиції в GitHub.")
    assert validate_email_message_output(v,payload("Re: issue #3496","Human discussion proposing Vetto"))["message_kind"] == "issue_discussion"

from core.memory_gateway_policy import validate_public_payload


def test_free_form_payload_is_json_bounded() -> None:
    payload = {"note": "alpha beta"}
    assert validate_public_payload(payload) == payload

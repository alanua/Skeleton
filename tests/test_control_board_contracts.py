from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from core.control_board.contracts import ControlBoardSnapshot, ControlBoardValidationError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "control_board" / "snapshot_v1.json"
SCHEMA = ROOT / "schemas" / "control_board_snapshot.schema.json"


def test_fixture_matches_schema_and_contract_model() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(payload)

    snapshot = ControlBoardSnapshot.from_mapping(payload)
    assert snapshot.to_mapping() == payload
    assert snapshot.schema == "skeleton.control_board.snapshot.v1"


def test_contract_rejects_unbounded_text() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["today"][0]["detail"] = "x" * 481

    with pytest.raises(ControlBoardValidationError):
        ControlBoardSnapshot.from_mapping(payload)


def test_fixture_is_synthetic_public_safe() -> None:
    text = FIXTURE.read_text(encoding="utf-8").lower()
    forbidden = ["gmail.com", "github_pat", "duckdns", "sqlite", "secret", "password"]

    for marker in forbidden:
        assert marker not in text

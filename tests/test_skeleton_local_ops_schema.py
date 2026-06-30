from __future__ import annotations

import json
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]


def test_local_memory_packet_schema() -> None:
    schema = json.loads((ROOT / "schemas" / "skeleton_local_memory_packet.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(
        {
            "schema": "skeleton.local_memory_packet.v1",
            "facts": [{"namespace": "project", "fact_id": "decision-001", "value": {"approved": True}}],
        },
        schema,
    )


def test_local_aufmass_schema() -> None:
    schema = json.loads((ROOT / "schemas" / "aufmass_local_input.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(
        {
            "schema": "skeleton.aufmass.local_input.v1",
            "project_ref": "project-001",
            "unit": "m",
            "rooms": [
                {
                    "room_id": "room-001",
                    "calculation_status": "accepted_input",
                    "polygon": [[0, 0], [4, 0], [4, 3], [0, 3]],
                    "height_m": 2.5,
                    "height_status": "confirmed",
                    "openings": [],
                    "source_evidence_refs": ["evidence-001"],
                }
            ],
        },
        schema,
    )

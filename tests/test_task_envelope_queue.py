from core.task_envelope_queue import parse_queue_request


def test_parse_queue_request() -> None:
    request = parse_queue_request(
        {
            "number": 42,
            "body": (
                "Mode: TASK_ENVELOPE\n"
                "Envelope Ref: task-001\n"
                + "Envelope SHA256: "
                + "a" * 64
                + "\n"
            ),
            "author": {"login": "alanua"},
        },
        trusted_authors=frozenset({"alanua"}),
    )

    assert request is not None
    assert request.issue_number == 42
    assert request.reference_id == "task-001"

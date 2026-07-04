from __future__ import annotations

from pathlib import Path

from core.private_memory_stack import PrivateMemoryStack


def test_local_private_memory_accepts_and_searches_operator_terms(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init()
    stack.put(
        namespace="operator.notes",
        fact_id="universal-terms",
        value={
            "summary": (
                "home address phone email legal contact private "
                "BauClock Aufmass"
            )
        },
    )

    for term in (
        "home",
        "address",
        "phone",
        "email",
        "legal",
        "contact",
        "private",
        "BauClock",
        "Aufmass",
    ):
        result = stack.search(query=term, limit=3)
        assert result["results"]
        assert result["results"][0]["canonical_ref"] == (
            "operator.notes:universal-terms"
        )

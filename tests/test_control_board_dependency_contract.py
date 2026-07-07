from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_control_board_test_dependencies_are_explicit() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = set(data["project"]["optional-dependencies"]["dev"])
    assert "fastapi==0.116.1" in deps
    assert "jinja2==3.1.6" in deps
    assert "uvicorn==0.35.0" in deps
    assert "httpx==0.28.1" in deps

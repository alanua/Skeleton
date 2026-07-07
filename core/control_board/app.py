from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.control_board.contracts import ControlBoardSnapshot, load_snapshot
from core.control_board.projections import project_view


PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parents[1]
DEFAULT_SNAPSHOT_PATH = ROOT_DIR / "fixtures" / "control_board" / "snapshot_v1.json"


def create_app(snapshot_path: Path = DEFAULT_SNAPSHOT_PATH) -> FastAPI:
    snapshot = load_snapshot(snapshot_path)
    return create_app_from_snapshot(snapshot)


def create_app_from_snapshot(snapshot: ControlBoardSnapshot) -> FastAPI:
    app = FastAPI(
        title="Skeleton Control Board",
        description="Read-only public-safe Skeleton Control Board",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        context = {"request": request, **project_view(snapshot)}
        return templates.TemplateResponse("index.html", context)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "schema": snapshot.schema}

    return app


app = create_app()

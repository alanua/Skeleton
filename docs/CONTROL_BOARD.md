# Skeleton Control Board

The Control Board is a read-only FastAPI and Jinja2 MVP backed only by `fixtures/control_board/snapshot_v1.json`. The fixture is synthetic public-safe data and does not connect to GitHub, Runner, MemoryGateway, n8n, Calendar, Gmail, SQLite, private files, or any live network service.

## Local Run

```bash
uvicorn core.control_board.app:app --host 127.0.0.1 --port 8080
```

Routes are read-only:

- `GET /`
- `GET /healthz`
- `GET /static/control-board.css`
- `GET /static/control-board.js`

## Compose

```bash
docker compose -f deploy/control_board/compose.yaml up --build
```

The Compose port binding is localhost-only: `127.0.0.1:8080:8080`. The deployment has one application container with an HTTP health check against `/healthz`.

## Tailscale Serve Notes

Do not run these commands from automation. If an operator chooses to expose the already-running local service over their private tailnet, they can review and run Tailscale Serve manually on the host:

```bash
tailscale serve --bg http://127.0.0.1:8080
tailscale serve status
```

No public hostname, DuckDNS, reverse proxy, Funnel, or Tailscale mutation is part of this repository change.

## Validation

Focused checks:

```bash
pytest tests/test_control_board_contracts.py tests/test_control_board_ui.py tests/test_control_board_deployment.py
python -m py_compile core/control_board/__init__.py core/control_board/app.py core/control_board/contracts.py core/control_board/projections.py
git diff --check
```

For full pytest, remove `SKELETON_HOME_EDGE_01_*` variables from the child environment before invocation.

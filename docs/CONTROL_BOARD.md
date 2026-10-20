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

The container image is pinned to:

```text
python:3.12.13-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b
```

That multi-platform digest was verified against the official `docker-library/repo-info` record `repos/python/remote/3.12.13-slim-bookworm.md` at commit `790c4e06530b08748dab58701b2f18e280d837ff`. The image copies only `core/control_board` and the synthetic Control Board fixture; it does not install or copy the rest of Skeleton.

The service runs as the dedicated `controlboard` user, drops all Linux capabilities, enables `no-new-privileges`, uses a read-only filesystem, and only mounts a bounded `/tmp` tmpfs. Compose also sets bounded CPU, memory, and process limits.

All app responses include restrictive response security headers:

- `Content-Security-Policy`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `Permissions-Policy`
- `X-Frame-Options: DENY`

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
pytest tests/test_control_board_dependency_contract.py tests/test_control_board_contracts.py tests/test_control_board_ui.py tests/test_control_board_deployment.py
python -m py_compile core/control_board/__init__.py core/control_board/app.py core/control_board/contracts.py core/control_board/projections.py
git diff --check
```

For full pytest, install the `dev` optional dependency group in an isolated environment and remove every `SKELETON_HOME_EDGE_01_*` variable from the child environment before invocation.

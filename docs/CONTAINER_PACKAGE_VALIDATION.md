# Container Package Validation

`Container Package Validation` is a public-safe GitHub Actions workflow for validating Docker and Docker Compose package changes on a disposable GitHub-hosted runner. It validates package shape and startup behavior only. It never deploys, imports credentials, opens a public listener, or contacts production, Hetzner, Home Edge, Tailscale, SSH, or cloud infrastructure.

The workflow is intentionally scoped to:

- `deploy/n8n/**`
- `deploy/control_board/**`
- this document
- `tests/test_container_package_validation_workflow.py`
- `.github/workflows/container-package-validation.yml`

Pull requests run only the package jobs implied by changed paths. A pull request changing `deploy/n8n/**` or `deploy/control_board/**` always runs that package job and fails closed if the changed package directory is unexpectedly absent. Changes only to this workflow, this document, or the workflow contract test run each package job only when that package directory exists in the checked-out commit. Manual dispatch accepts one input, `commit_sha`, and fails unless it is an exact 40-character SHA that can be checked out from this repository. Manual dispatch validates every registered package present at the requested commit and fails when neither registered package exists.

## Security Boundary

The workflow uses `permissions: contents: read` only and does not use `pull_request_target`. It does not read repository, environment, or organization secrets. It does not write caches or upload artifacts. Runtime values are synthetic and disposable.

Validation is limited to GitHub-hosted Docker and Docker Compose. Compose services are rejected when they request privileged mode, host networking, the Docker socket, broad host mounts, or non-loopback port bindings. All Compose runs use per-run project names and unconditional cleanup with `docker compose down --volumes --remove-orphans`, project-labelled network pruning, and temporary file deletion.

Scope logs are limited to aggregate package-presence and package-scope booleans. Validation logs are limited to aggregate status, pinned image digests, health state, and test totals. The workflow does not print environment files, Compose configuration, secrets, or package runtime configuration.

## n8n Validation

The n8n path validates:

- shell syntax for package shell scripts
- focused n8n tests after installing the bounded Python set `PyYAML>=6.0.0,<7.0.0` and `pytest>=8.0.0,<9.0.0`
- UID/GID `1000:1000` ownership behavior through a stripped environment and a portable UID/GID drop using `setpriv`, without the GitHub-hosted-runner-incompatible network namespace operation
- a disposable test home and temporary directory owned by UID/GID `1000:1000`
- a synthetic owner-only Compose environment containing only disposable paths and a generated validation key
- exact image digest pinning from `docker compose config --images`
- `docker compose config --quiet`
- loopback-only disposable startup
- bounded health wait requiring every service to be running and every non-empty Compose health value to be `healthy`
- restart persistence
- unconditional cleanup of containers, networks, volumes, synthetic environment files, and temporary directories

The workflow never supplies a production key, workflow, credential, Docker socket, host-network mode, or broad host mount.

## Control Board Validation

The Control Board path validates:

- an isolated virtual environment populated from an explicit pinned dependency set matching the repository dependency contract
- focused Control Board dependency-contract, data-contract, UI, and deployment tests
- zero Control Board skips
- exact Dockerfile base-image digest pinning
- Docker Compose build
- `docker compose config --quiet`
- loopback-only disposable health requiring every service to be running and every non-empty Compose health value to be `healthy`
- unconditional cleanup of containers, networks, volumes, and temporary files

The explicit install avoids editable installation and flat-layout package auto-discovery while retaining `PYTHONPATH` only for the checked-out public repository under test.

## Local Contract Checks

Run the workflow contract test before changing the workflow:

```bash
python3 -m pytest tests/test_container_package_validation_workflow.py
```

The expected validation set for this change class is:

```bash
python3 -m pytest tests/test_container_package_validation_workflow.py
python3 -m pytest
python3 - <<'PY'
import pathlib
import yaml
yaml.safe_load(pathlib.Path(".github/workflows/container-package-validation.yml").read_text())
PY
git diff --check
```

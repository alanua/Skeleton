# Quickstart: verify Skeleton safely

This quickstart exercises public control-plane contracts only. It does not connect to Home Edge, deploy services, access credentials, or mutate external systems.

## Requirements

- Git
- Python 3.11+

## 1. Clone

```bash
git clone https://github.com/alanua/Skeleton.git
cd Skeleton
```

## 2. Create an isolated environment

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install pytest PyYAML
```

On Windows PowerShell, activate with:

```powershell
.venv\Scripts\Activate.ps1
```

## 3. Run the public control-plane smoke tests

```bash
python -m pytest -q \
  tests/test_project_tree.py \
  tests/test_project_loader.py \
  tests/test_project_manifests.py \
  tests/test_boot_manifest.py
```

These tests validate project routing, manifests, and the canonical boot contract without requiring private runtime configuration.

## 4. Inspect the control path

Start with:

```text
BOOT_MANIFEST.yaml
PROJECT_INDEX.yaml
PROJECT_TREE.yaml
EXECUTOR_REGISTRY.yaml
MEMORY_ROUTING.yaml
OPERATOR_RULES.yaml
```

Then read:

- `docs/ACTION_GATE.md`
- `docs/AUDIT_LEDGER.md`
- `docs/THREAT_MODEL.md`
- `docs/PROJECT_IMPACT.md`

## What not to do

A public checkout is not sufficient authority to operate a private Home Edge node or other production environment. Runtime execution requires its separately configured identities, approvals, private state, and registered execution path.

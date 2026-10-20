#!/usr/bin/env sh
set -eu

python -m py_compile \
  core/control_board/__init__.py \
  core/control_board/app.py \
  core/control_board/contracts.py \
  core/control_board/projections.py

python -m pytest -q \
  tests/test_control_board_dependency_contract.py \
  tests/test_control_board_contracts.py \
  tests/test_control_board_ui.py \
  tests/test_control_board_deployment.py

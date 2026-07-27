from __future__ import annotations

from core.cognee_validation_error_detail import (
    install_pydantic_validation_error_detail,
)
from core.cognee_worker_bootstrap import configure_cognee_worker_environment

install_pydantic_validation_error_detail()
configure_cognee_worker_environment()

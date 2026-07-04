from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.runner_executors import ExecutionContext


class RuntimeContextError(ValueError):
    pass

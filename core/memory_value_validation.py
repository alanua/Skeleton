from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping

MAX_DEPTH = 32
MAX_NODES = 10_000
MAX_KEYS = 5_000
MAX_TEXT_BYTES = 1_048_576
MAX_KEY_BYTES = 4_096


class MemoryValueError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def validate_memory_value(value: Any) -> Any:
    result = deepcopy(value)
    counters = {"nodes": 0, "keys": 0, "text_bytes": 0}
    _validate(result, path="memory", depth=0, counters=counters)
    return result


def _validate(value: Any, *, path: str, depth: int, counters: dict[str, int]) -> None:
    if depth > MAX_DEPTH:
        raise MemoryValueError("VALUE_LIMIT_EXCEEDED", f"{path} exceeds maximum depth")
    counters["nodes"] += 1
    if counters["nodes"] > MAX_NODES:
        raise MemoryValueError("VALUE_LIMIT_EXCEEDED", "maximum node count exceeded")
    if isinstance(value, Mapping):
        counters["keys"] += len(value)
        if counters["keys"] > MAX_KEYS:
            raise MemoryValueError("VALUE_LIMIT_EXCEEDED", "maximum key count exceeded")
        for key, child in value.items():
            if not isinstance(key, str):
                raise MemoryValueError("INVALID_VALUE", f"{path} has a non-string key")
            if len(key.encode("utf-8")) > MAX_KEY_BYTES:
                raise MemoryValueError("VALUE_LIMIT_EXCEEDED", "maximum key size exceeded")
            _validate(child, path=f"{path}[{key!r}]", depth=depth + 1, counters=counters)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate(child, path=f"{path}[{index}]", depth=depth + 1, counters=counters)
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MemoryValueError("INVALID_VALUE", f"{path} has a non-finite number")
        return
    if isinstance(value, str):
        counters["text_bytes"] += len(value.encode("utf-8"))
        if counters["text_bytes"] > MAX_TEXT_BYTES:
            raise MemoryValueError("VALUE_LIMIT_EXCEEDED", "maximum text size exceeded")
        return
    raise MemoryValueError("INVALID_VALUE", f"{path} is not JSON-compatible")

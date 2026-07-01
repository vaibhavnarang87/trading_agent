"""
Audit trail.

Every proposal, every governor decision, every approval outcome, and every fill
is recorded as a structured JSON line. This is the replayable record of what the
agent did and why. Nothing should touch a broker without a corresponding entry.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any


def _coerce(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _coerce(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _coerce(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_coerce(v) for v in obj]
    if hasattr(obj, "value"):  # Enum
        return obj.value
    return obj


class AuditLog:
    def __init__(self, stream=sys.stdout):
        self.stream = stream

    def record(self, event: str, **fields: Any) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **{k: _coerce(v) for k, v in fields.items()},
        }
        self.stream.write(json.dumps(entry) + "\n")
        self.stream.flush()

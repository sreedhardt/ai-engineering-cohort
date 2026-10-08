"""Structured event logging.

Guardrail decisions and request metrics are written as JSON Lines, one object
per event, so they can be queried after the fact:

    jq 'select(.event=="guardrail" and .decision=="block")' logs/events.jsonl

Console output is a human-readable mirror; the JSONL file is the record. A
reviewer must be able to reconstruct what the system saw, what it decided and
why, from these events plus the LangSmith trace, without re-running anything.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evalguard.config import LOGS_DIR

_lock = threading.Lock()
logger = logging.getLogger("evalguard")

EVENT_LOG = LOGS_DIR / "events.jsonl"


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def emit(event: str, **fields: Any) -> dict:
    """Write one structured event. Returns the record for convenience."""
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        **fields,
    }
    line = json.dumps(record, default=str)

    with _lock:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with EVENT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    logger.info("%s %s", event, line)
    return record


def read_events(event: str | None = None, request_id: str | None = None) -> list[dict]:
    """Read events back — used by the report and by tests."""
    if not EVENT_LOG.exists():
        return []
    out = []
    for raw in EVENT_LOG.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event and record.get("event") != event:
            continue
        if request_id and record.get("request_id") != request_id:
            continue
        out.append(record)
    return out

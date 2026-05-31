"""The audit trail must hold up to an auditor. Two principles drive this module:

  1. PII never enters the log. We serialize only types, counts, hashes, and
     decisions -- never the original text, never the real values behind
     placeholders. The in-memory mapping stays in the context and is discarded.

  2. Storage is swappable. Everything goes through AuditRepository, so "local
     JSON now, real database later" is a one-line config change with no impact
     on the rest of the system (Repository pattern).
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..pipeline.context import GovernanceContext


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_audit_record(context: GovernanceContext) -> dict:
    """Build a PII-free, auditor-safe record from a context.

    We log *that* an email was found and pseudonymized, not the email itself.
    Input is represented only by its hash, which proves integrity without
    revealing content.
    """
    entity_type_counts = Counter(e.entity_type for e in context.entities)
    return {
        "request_id": context.request_id,
        "created_at": context.created_at,
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": _sha256(context.original_input),
        "input_length": len(context.original_input),
        "decision": context.decision.value,
        "block_reasons": context.block_reasons,
        # Counts by type only -- no values, no offsets that could leak content.
        "entities_by_type": dict(entity_type_counts),
        "entity_total": len(context.entities),
        # The full stage-by-stage history (each summary is already PII-free).
        "history": [
            {
                "stage": r.stage_name,
                "timestamp": r.timestamp,
                "summary": r.summary,
            }
            for r in context.history
        ],
    }


class AuditRepository(ABC):
    @abstractmethod
    def save(self, context: GovernanceContext) -> str:
        """Persist an audit record. Returns an identifier/locator."""
        raise NotImplementedError


class InMemoryAuditRepository(AuditRepository):
    """Keeps records in a list. Useful for tests."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def save(self, context: GovernanceContext) -> str:
        record = to_audit_record(context)
        self.records.append(record)
        return record["request_id"]


class JsonFileAuditRepository(AuditRepository):
    """Writes one JSON file per request into a directory.

    Append-only by convention: we never modify an existing file. For a real
    deployment you would layer on write-once storage / hash chaining, but the
    interface here already isolates that concern.
    """

    def __init__(self, directory: str = "./audit_logs") -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, context: GovernanceContext) -> str:
        record = to_audit_record(context)
        out = self.directory / f"{record['request_id']}.json"
        out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(out)

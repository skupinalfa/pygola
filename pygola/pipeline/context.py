"""The GovernanceContext is the single object that flows through every stage
of the pipeline. Each stage reads from it and enriches it. By the end of the
run it contains the full story of what happened to a request -- which is also
the basis for the audit trail.

Design note: we deliberately keep PII *values* out of anything that gets
persisted. The `mapping` (pseudonym -> real value) is held in memory only and
is what allows us to re-insert real values into the downstream response. It is
never written to the audit log.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Decision(str, Enum):
    """The outcome of the pipeline for a given request."""

    PENDING = "pending"        # still being processed
    ALLOW = "allow"            # cleared to go to the commercial LLM
    BLOCK = "block"            # must not be forwarded
    NEEDS_CONFIRM = "needs_confirm"  # waiting on a human decision


@dataclass
class DetectedEntity:
    """A single piece of sensitive data found in the input.

    Note we store the *type* and a *placeholder*, plus offsets, but we keep the
    real value separate (in the context mapping) so it never leaks into logs.
    """

    entity_type: str          # e.g. "EMAIL", "IBAN", "PERSON"
    start: int                # char offset in the original text
    end: int
    placeholder: str          # e.g. "[EMAIL_1]"
    score: float = 1.0        # detector confidence (1.0 for deterministic)
    source: str = "deterministic"  # which stage found it


@dataclass
class StageRecord:
    """An immutable record of what one stage did. This is the audit unit."""

    stage_name: str
    timestamp: str
    summary: dict[str, Any]   # PII-free description of what happened


@dataclass
class GovernanceContext:
    """Carries a request through the pipeline and accumulates its history."""

    # --- identity & timing ---
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # --- the text as it evolves ---
    original_input: str = ""          # what the user sent in (sensitive!)
    sanitized_input: str = ""         # pseudonymized, safe to forward
    downstream_output: str = ""       # raw answer from commercial LLM
    final_output: str = ""            # answer with real values re-inserted

    # --- findings & decisions ---
    entities: list[DetectedEntity] = field(default_factory=list)
    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> real value (IN MEMORY ONLY)
    decision: Decision = Decision.PENDING
    block_reasons: list[str] = field(default_factory=list)

    # --- audit trail ---
    history: list[StageRecord] = field(default_factory=list)

    def record(self, stage_name: str, summary: dict[str, Any]) -> None:
        """Append a PII-free record of a stage's work to the history."""
        self.history.append(
            StageRecord(
                stage_name=stage_name,
                timestamp=datetime.now(timezone.utc).isoformat(),
                summary=summary,
            )
        )

    def block(self, reason: str) -> None:
        self.decision = Decision.BLOCK
        self.block_reasons.append(reason)

"""The deterministic detection layer -- the auditable foundation.

These regex patterns reliably and *reproducibly* catch structured PII. The
same input always yields the same findings, which is exactly what an auditor
needs. This is intentionally a starting set; in production you would swap or
augment this stage with Microsoft Presidio (which adds NER + many validated
recognizers) behind the very same Stage interface.

This stage only *detects and records*. The decision of what to do with each
finding (pseudonymize vs. block) is made later, driven by the policy config.
"""

from __future__ import annotations

import re

from ..pipeline.context import DetectedEntity, GovernanceContext
from ..pipeline.stage import Stage

# Each pattern maps an entity_type to a compiled regex. Conservative on
# precision; recall is later reinforced by the trusted-LLM stage.
_PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL_ADDRESS": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    # Loose international-ish phone matcher.
    "PHONE_NUMBER": re.compile(r"(?<!\w)(?:\+?\d[\d\s/().-]{7,}\d)(?!\w)"),
    # IBAN: 2 letters, 2 digits, up to 30 alphanumerics.
    "IBAN_CODE": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    # Credit-card-like 13-19 digit groups (validation would come from Presidio).
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}


class DeterministicPiiStage(Stage):
    name = "deterministic_pii_scan"

    def process(self, context: GovernanceContext) -> GovernanceContext:
        text = context.original_input
        found: list[DetectedEntity] = []

        for entity_type, pattern in _PATTERNS.items():
            for match in pattern.finditer(text):
                found.append(
                    DetectedEntity(
                        entity_type=entity_type,
                        start=match.start(),
                        end=match.end(),
                        placeholder="",  # assigned later, in pseudonymization
                        score=1.0,
                        source="deterministic",
                    )
                )

        context.entities.extend(found)
        context.record(
            self.name,
            {
                "detector": "regex",
                "matches_by_type": _counts(found),
                "match_total": len(found),
            },
        )
        return context


def _counts(entities: list[DetectedEntity]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in entities:
        out[e.entity_type] = out.get(e.entity_type, 0) + 1
    return out

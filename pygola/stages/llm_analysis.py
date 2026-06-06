"""The trusted-LLM analysis stage. This is the *second* detection layer: it
asks the trusted provider to flag contextual sensitive information that pattern
matching cannot see (e.g. "the patient in room 3 with the rare condition").

Crucial design rule, as discussed: this layer can only ADD findings. It can
never downgrade a BLOCK to an ALLOW. The deterministic layer and the policy
remain authoritative. The LLM augments recall; it never relaxes the policy.
"""

from __future__ import annotations

from ..config.schema import PolicyConfig
from ..pipeline.context import DetectedEntity, GovernanceContext
from ..pipeline.stage import Stage
from ..providers.base import LLMProvider, _PII_SYSTEM_PROMPT


class LlmAnalysisStage(Stage):
    name = "trusted_llm_analysis"

    def __init__(self, provider: LLMProvider, policy: PolicyConfig):
        self.provider = provider
        self.policy = policy

    def process(self, context: GovernanceContext) -> GovernanceContext:
        provider_name = self.provider.name
        model_id = getattr(self.provider, "_model", "n/a")

        if not self.policy.llm_analysis_enabled:
            context.llm_calls.append({
                "role": "trusted",
                "skipped": True,
                "provider": provider_name,
                "model": model_id,
                "messages": [],
                "response": "",
            })
            context.record(self.name, {"skipped": True, "reason": "disabled in policy"})
            return context

        text = context.original_input
        spans = self.provider.find_contextual_pii(text)

        context.llm_calls.append({
            "role": "trusted",
            "skipped": False,
            "provider": provider_name,
            "model": model_id,
            "messages": [
                {"role": "system", "content": _PII_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "response": "\n".join(spans),
        })

        added = 0
        # Avoid double-counting spans the deterministic layer already covered.
        existing = {(e.start, e.end) for e in context.entities}
        for span in spans:
            idx = text.find(span)
            if idx == -1:
                continue
            pos = (idx, idx + len(span))
            if pos in existing:
                continue
            context.entities.append(
                DetectedEntity(
                    entity_type="PERSON",  # mock heuristic flags name-like spans
                    start=pos[0],
                    end=pos[1],
                    placeholder="",
                    score=0.7,
                    source="trusted_llm",
                )
            )
            existing.add(pos)
            added += 1

        context.record(
            self.name,
            {
                "provider": self.provider.name,
                "contextual_findings_added": added,
                "prompt": context.original_input,
            },
        )
        return context

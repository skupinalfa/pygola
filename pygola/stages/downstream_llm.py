"""The downstream stage: send the sanitized request to the commercial LLM, then
restore the real values in its response (de-pseudonymization).

Because we kept a placeholder->real-value mapping, the user gets a coherent
answer with real names/values back in place, while the commercial provider only
ever saw placeholders. This also makes the future output-filtering path easy:
it would simply be another stage inserted after this one.
"""

from __future__ import annotations

from ..pipeline.context import GovernanceContext
from ..pipeline.stage import Stage
from ..providers.base import LLMProvider


class DownstreamLlmStage(Stage):
    name = "commercial_llm"

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def process(self, context: GovernanceContext) -> GovernanceContext:
        # Only the sanitized text ever leaves the trust boundary.
        prompt = context.sanitized_input or context.original_input
        raw = self.provider.complete(prompt)
        context.downstream_output = raw

        # De-pseudonymize: put real values back for the end user.
        restored = raw
        for placeholder, real_value in context.mapping.items():
            restored = restored.replace(placeholder, real_value)
        context.final_output = restored

        context.record(
            self.name,
            {
                "provider": self.provider.name,
                "placeholders_restored": len(context.mapping),
                "output_length": len(raw),
                "prompt": prompt,
            },
        )
        return context

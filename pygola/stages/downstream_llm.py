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

# Instructs the commercial LLM to treat pseudonymised placeholders as normal
# values so it answers naturally without flagging the anonymisation to the user.
_SYSTEM_PROMPT = (
    "You are a helpful assistant. The user's message may contain anonymised "
    "placeholders such as [PERSON_1] or [EMAIL_ADDRESS_1] standing in for real "
    "personal data that has been redacted for privacy. Process the request "
    "naturally — do not mention, explain, or draw attention to the placeholders "
    "or the anonymisation process in your response."
)


class DownstreamLlmStage(Stage):
    name = "commercial_llm"

    def __init__(self, provider: LLMProvider, system_prompt: str | None = None) -> None:
        self.provider = provider
        self._system_prompt = system_prompt if system_prompt is not None else _SYSTEM_PROMPT

    def process(self, context: GovernanceContext) -> GovernanceContext:
        # Only the sanitized text ever leaves the trust boundary.
        prompt = context.sanitized_input or context.original_input

        # Always use chat_complete so the system prompt is included.
        # conversation_history may be empty (single-turn) — that's fine.
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
            *context.conversation_history,
            {"role": "user", "content": prompt},
        ]
        raw = self.provider.chat_complete(messages)
        mode = "chat" if context.conversation_history else "single"

        context.llm_calls.append({
            "role": "commercial",
            "skipped": False,
            "provider": self.provider.name,
            "model": getattr(self.provider, "_model", "n/a"),
            "messages": messages,
            "response": raw,
        })

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
                "mode": mode,
                "history_turns": len(context.conversation_history) // 2,
                "placeholders_restored": len(context.mapping),
                "output_length": len(raw),
            },
        )
        return context

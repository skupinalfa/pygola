"""LLM providers sit behind a single interface so the pipeline never depends on
a concrete vendor. Today we ship a deterministic Mock provider so the whole
system runs with no API key. Real providers (OpenAI, Anthropic, local) slot in
later by implementing the same interface -- nothing else changes.

Two roles use providers:
  - the *trusted* provider: used by the analysis stage to catch contextual PII
    the deterministic layer missed. It must be local or a vetted vendor.
  - the *commercial* provider: the downstream model that actually answers the
    (sanitized) request.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterator

# Shared system prompt for contextual PII detection.
# It is stable across calls, so the Anthropic provider marks it for caching.
_PII_SYSTEM_PROMPT = (
    "You are a privacy compliance assistant. "
    "Identify personal or sensitive information in the user's text that "
    "pattern-based detectors (regex / NER) might miss — full names, "
    "job-title + organisation combinations, indirect identifiers, or "
    "context-specific sensitive details.\n\n"
    "Return ONLY the exact spans from the input text, one per line. "
    "No labels, no explanations, no formatting. "
    "If nothing sensitive is found, return an empty response."
)


def _parse_pii_spans(response: str) -> list[str]:
    return [line.strip() for line in response.strip().splitlines() if line.strip()]


class LLMProvider(ABC):
    name: str = "unnamed_provider"
    supports_streaming: bool = False
    supports_chat_complete: bool = False

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Return the model's completion for a prompt."""
        raise NotImplementedError

    def streaming_complete(self, prompt: str) -> Iterator[str]:
        """Yield completion tokens incrementally.

        Override and set supports_streaming = True on subclasses that support
        streaming. The base implementation always raises NotImplementedError so
        callers can check supports_streaming before calling this.
        """
        raise NotImplementedError(
            f"Provider '{self.name}' does not support streaming. "
            "Check provider.supports_streaming before calling streaming_complete()."
        )

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        """Return the model's reply for a multi-turn messages list.

        *messages* follows the OpenAI/Anthropic role-content format:
        ``[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]``

        Override and set supports_chat_complete = True on subclasses that
        natively support structured history. The base implementation raises
        NotImplementedError so callers can check supports_chat_complete first.
        """
        raise NotImplementedError(
            f"Provider '{self.name}' does not support chat_complete. "
            "Check provider.supports_chat_complete before calling."
        )

    def find_contextual_pii(self, text: str) -> list[str]:
        """Optional: return spans of text the trusted model thinks are sensitive
        but that pattern-based detection might miss. Default: nothing.
        """
        return []


class MockProvider(LLMProvider):
    """A deterministic stand-in. No network, no keys -- ideal for development
    and for tests that must be reproducible.
    """

    name = "mock"
    supports_streaming = True
    supports_chat_complete = True

    def complete(self, prompt: str) -> str:
        # Echoes back in a predictable way so you can trace the flow end to end.
        return f"[mock completion] received {len(prompt)} chars."

    def streaming_complete(self, prompt: str) -> Iterator[str]:
        for word in self.complete(prompt).split():
            yield word + " "

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        n_turns = sum(1 for m in messages if m.get("role") == "user")
        return f"[mock chat response for {n_turns} user turn(s)]"

    def find_contextual_pii(self, text: str) -> list[str]:
        # Trivial heuristic for demonstration: flag capitalized two-word spans
        # that look like names but weren't already bracketed as placeholders.
        candidates = re.findall(r"\b[A-ZÄÖÜ][a-zäöü]+\s+[A-ZÄÖÜ][a-zäöü]+\b", text)
        return [c for c in candidates if not c.startswith("[")]


class AnthropicProvider(LLMProvider):
    """Calls the Anthropic Messages API.

    Requires the 'anthropic' package:  pip install 'governance-layer[anthropic]'
    The API key is read from the environment variable named in ProviderConfig.api_key_env.
    """

    name = "anthropic"
    supports_streaming = True
    supports_chat_complete = True

    def __init__(self, api_key: str, model: str) -> None:
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for the Anthropic provider. "
                "Install it with:  pip install 'governance-layer[anthropic]'"
            ) from exc
        self._client = _anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(self, prompt: str) -> str:
        import anthropic as _anthropic
        from .retry import retry_with_backoff
        from .errors import RateLimitError, TimeoutError as ConnectorTimeout

        def _call() -> str:
            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=16000,
                    messages=[{"role": "user", "content": prompt}],
                )
                return next(b.text for b in response.content if b.type == "text")
            except _anthropic.RateLimitError as exc:
                raise RateLimitError(str(exc)) from exc
            except _anthropic.APITimeoutError as exc:
                raise ConnectorTimeout(str(exc)) from exc

        return retry_with_backoff(_call)

    def streaming_complete(self, prompt: str) -> Iterator[str]:
        with self._client.messages.stream(
            model=self._model,
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                yield text

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        import anthropic as _anthropic
        from .retry import retry_with_backoff
        from .errors import RateLimitError, TimeoutError as ConnectorTimeout

        # Anthropic uses a dedicated 'system' parameter; extract it if present.
        system: str | None = None
        chat_messages = messages
        if messages and messages[0].get("role") == "system":
            system = messages[0]["content"]
            chat_messages = messages[1:]

        def _call() -> str:
            try:
                kwargs: dict = dict(model=self._model, max_tokens=4096, messages=chat_messages)
                if system:
                    kwargs["system"] = system
                response = self._client.messages.create(**kwargs)
                return next(b.text for b in response.content if b.type == "text")
            except _anthropic.RateLimitError as exc:
                raise RateLimitError(str(exc)) from exc
            except _anthropic.APITimeoutError as exc:
                raise ConnectorTimeout(str(exc)) from exc

        return retry_with_backoff(_call)

    def find_contextual_pii(self, text: str) -> list[str]:
        # The system prompt is identical on every call — mark it for caching so
        # repeated analysis requests only pay for the user-turn tokens.
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": _PII_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": text}],
        )
        return _parse_pii_spans(
            next((b.text for b in response.content if b.type == "text"), "")
        )


class OpenAIProvider(LLMProvider):
    """Calls the OpenAI Chat Completions API.

    Requires the 'openai' package:  pip install 'governance-layer[openai]'
    The API key is read from the environment variable named in ProviderConfig.api_key_env.
    """

    name = "openai"
    supports_chat_complete = True

    def __init__(self, api_key: str, model: str) -> None:
        try:
            import openai as _openai
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for the OpenAI provider. "
                "Install it with:  pip install 'governance-layer[openai]'"
            ) from exc
        self._client = _openai.OpenAI(api_key=api_key)
        self._model = model

    def complete(self, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=4096,
            messages=messages,
        )
        return response.choices[0].message.content or ""

    def find_contextual_pii(self, text: str) -> list[str]:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": _PII_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )
        return _parse_pii_spans(response.choices[0].message.content or "")

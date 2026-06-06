"""Local LLM adapter — connects to any OpenAI-compatible server running locally.

Targets servers such as Ollama, LM Studio, vLLM, and LocalAI that expose an
OpenAI-compatible REST API. Uses the OpenAI SDK with a configurable base_url.

This is a standalone adapter; it does not inherit from or compose OpenAIProvider
so it can evolve independently (local-specific concerns like server-availability
checks stay local and do not bleed into the OpenAI cloud integration).

Default base_url:  http://localhost:11434/v1  (Ollama)
Authentication:    no real key required; the dummy string "local" is used when
                   api_key_env is not set in ProviderConfig.
"""

from __future__ import annotations

from collections.abc import Iterator

from .base import _PII_SYSTEM_PROMPT, _parse_pii_spans, LLMProvider
from .errors import ProviderUnavailableError


_DEFAULT_BASE_URL = "http://localhost:11434/v1"
_DUMMY_API_KEY = "local"


class LocalProvider(LLMProvider):
    """Adapter for locally-hosted OpenAI-compatible LLM servers.

    Instantiate directly or let the factory / registry create it from config.
    """

    name = "local"
    supports_streaming = True
    supports_chat_complete = True

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        try:
            import openai as _openai
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for the local provider. "
                "Install it with:  pip install 'governance-layer[local]'"
            ) from exc

        self._base_url = base_url
        self._model = model
        self._client = _openai.OpenAI(base_url=base_url, api_key=api_key)

    def complete(self, prompt: str) -> str:
        try:
            import openai as _openai
        except ImportError:
            raise  # already handled in __init__

        from .retry import retry_with_backoff
        from .errors import RateLimitError, TimeoutError as ConnectorTimeout

        def _call() -> str:
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.choices[0].message.content or ""
            except _openai.APIConnectionError as exc:
                raise ProviderUnavailableError(self._base_url, cause=exc) from exc
            except _openai.RateLimitError as exc:
                raise RateLimitError(str(exc)) from exc
            except _openai.APITimeoutError as exc:
                raise ConnectorTimeout(str(exc)) from exc

        return retry_with_backoff(_call)

    def streaming_complete(self, prompt: str) -> Iterator[str]:
        try:
            import openai as _openai
        except ImportError:
            raise

        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta is not None:
                    yield delta
        except _openai.APIConnectionError as exc:
            raise ProviderUnavailableError(self._base_url, cause=exc) from exc

    def chat_complete(self, messages: list[dict[str, str]]) -> str:
        try:
            import openai as _openai
        except ImportError:
            raise

        from .retry import retry_with_backoff
        from .errors import RateLimitError, TimeoutError as ConnectorTimeout

        def _call() -> str:
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                )
                return response.choices[0].message.content or ""
            except _openai.APIConnectionError as exc:
                raise ProviderUnavailableError(self._base_url, cause=exc) from exc
            except _openai.RateLimitError as exc:
                raise RateLimitError(str(exc)) from exc
            except _openai.APITimeoutError as exc:
                raise ConnectorTimeout(str(exc)) from exc

        return retry_with_backoff(_call)

    def find_contextual_pii(self, text: str) -> list[str]:
        try:
            import openai as _openai
        except ImportError:
            raise

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _PII_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
            )
            return _parse_pii_spans(response.choices[0].message.content or "")
        except _openai.APIConnectionError as exc:
            raise ProviderUnavailableError(self._base_url, cause=exc) from exc

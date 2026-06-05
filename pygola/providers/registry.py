"""Provider registry — maps provider kind strings to factory callables.

The registry decouples provider discovery from the factory function, so third
parties can register custom LLM connectors without modifying source code:

    from pygola.providers.registry import DEFAULT_REGISTRY
    from pygola.config.schema import ProviderConfig

    DEFAULT_REGISTRY.register("myprovider", lambda cfg: MyProvider(cfg))

    # Then in config:
    #   kind: myprovider
    #   model: my-model

Factory callables receive the full ProviderConfig so they can implement any
key-generation or init-time logic they require (env lookup, keychain, JWT,
auth service call) without any changes to this module.
"""

from __future__ import annotations

import os
from typing import Callable

from ..config.schema import ProviderConfig
from .base import LLMProvider, MockProvider, AnthropicProvider, OpenAIProvider
from .errors import ProviderUnavailableError


ProviderFactory = Callable[[ProviderConfig], LLMProvider]


def _resolve_api_key(cfg: ProviderConfig) -> str:
    key = os.environ.get(cfg.api_key_env)
    if key is None:
        raise RuntimeError(
            f"Provider '{cfg.kind}' requires the environment variable "
            f"'{cfg.api_key_env}' to be set."
        )
    return key


def _build_mock(cfg: ProviderConfig) -> LLMProvider:
    return MockProvider()


def _build_anthropic(cfg: ProviderConfig) -> LLMProvider:
    return AnthropicProvider(api_key=_resolve_api_key(cfg), model=cfg.model)


def _build_openai(cfg: ProviderConfig) -> LLMProvider:
    return OpenAIProvider(api_key=_resolve_api_key(cfg), model=cfg.model)


def _build_local(cfg: ProviderConfig) -> LLMProvider:
    from .local import LocalProvider, _DEFAULT_BASE_URL, _DUMMY_API_KEY

    local_key = os.environ.get(cfg.api_key_env, _DUMMY_API_KEY)
    local_url = cfg.base_url or _DEFAULT_BASE_URL
    return LocalProvider(base_url=local_url, api_key=local_key, model=cfg.model)


class ProviderRegistry:
    """Maps provider kind strings to factory callables.

    Use register() to add new providers; build() to construct one from config.
    """

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, kind: str, factory: ProviderFactory) -> None:
        """Register a factory for the given provider kind.

        The factory receives the full ProviderConfig and must return an
        LLMProvider instance. Calling register() with an existing kind
        replaces the previous factory (last registration wins).
        """
        self._factories[kind] = factory

    def build(self, cfg: ProviderConfig) -> LLMProvider:
        """Build a provider instance from config.

        Raises NotImplementedError if the kind has no registered factory.
        """
        factory = self._factories.get(cfg.kind)
        if factory is None:
            registered = ", ".join(sorted(self._factories)) or "(none)"
            raise NotImplementedError(
                f"Provider kind '{cfg.kind}' is not registered. "
                f"Registered kinds: {registered}. "
                "Call registry.register() to add a new provider."
            )
        return factory(cfg)

    @classmethod
    def default(cls) -> "ProviderRegistry":
        """Return a registry pre-loaded with all built-in provider factories."""
        registry = cls()
        registry.register("mock", _build_mock)
        registry.register("anthropic", _build_anthropic)
        registry.register("openai", _build_openai)
        registry.register("local", _build_local)
        return registry


DEFAULT_REGISTRY: ProviderRegistry = ProviderRegistry.default()

"""Small factories that turn config objects into concrete instances. Keeping
these in one place means adding a new provider or audit backend later is a
localized change.
"""

from __future__ import annotations

import os

from .audit.repository import (
    AuditRepository,
    InMemoryAuditRepository,
    JsonFileAuditRepository,
)
from .config.schema import AuditConfig, ProviderConfig
from .providers.base import AnthropicProvider, LLMProvider, MockProvider, OpenAIProvider


def resolve_api_key(cfg: ProviderConfig) -> str:
    """Read the API key from the environment. Fails closed: raises RuntimeError
    if the variable is absent so the application never starts in a broken state.
    """
    key = os.environ.get(cfg.api_key_env)
    if key is None:
        raise RuntimeError(
            f"Provider '{cfg.kind}' requires the environment variable "
            f"'{cfg.api_key_env}' to be set. "
            "Export it before starting the application — "
            "never store secret keys in config files or source code."
        )
    return key


def build_provider(cfg: ProviderConfig) -> LLMProvider:
    if cfg.kind == "mock":
        return MockProvider()

    api_key = resolve_api_key(cfg)  # fail-closed: raises RuntimeError if key is missing

    if cfg.kind == "anthropic":
        return AnthropicProvider(api_key=api_key, model=cfg.model)

    if cfg.kind == "openai":
        return OpenAIProvider(api_key=api_key, model=cfg.model)

    raise NotImplementedError(
        f"Provider kind '{cfg.kind}' is not implemented yet. "
        "Add a class implementing LLMProvider and wire it up here."
    )


def build_audit_repository(cfg: AuditConfig) -> AuditRepository:
    if cfg.backend == "memory":
        return InMemoryAuditRepository()
    if cfg.backend == "jsonfile":
        return JsonFileAuditRepository(directory=cfg.path)
    raise NotImplementedError(f"Audit backend '{cfg.backend}' is not implemented.")

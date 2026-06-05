"""Small factories that turn config objects into concrete instances.

build_provider() delegates to the ProviderRegistry so new providers can be
added via registry.register() without modifying this file.
"""

from __future__ import annotations

from .audit.repository import (
    AuditRepository,
    InMemoryAuditRepository,
    JsonFileAuditRepository,
)
from .config.schema import AuditConfig, ProviderConfig
from .providers.base import LLMProvider
from .providers.registry import DEFAULT_REGISTRY


def build_provider(cfg: ProviderConfig) -> LLMProvider:
    """Build a provider from config by dispatching through the DEFAULT_REGISTRY.

    To add a custom provider without modifying this file:

        from pygola.providers.registry import DEFAULT_REGISTRY
        DEFAULT_REGISTRY.register("mykind", lambda cfg: MyProvider(cfg))
    """
    return DEFAULT_REGISTRY.build(cfg)


def build_audit_repository(cfg: AuditConfig) -> AuditRepository:
    if cfg.backend == "memory":
        return InMemoryAuditRepository()
    if cfg.backend == "jsonfile":
        return JsonFileAuditRepository(directory=cfg.path)
    raise NotImplementedError(f"Audit backend '{cfg.backend}' is not implemented.")

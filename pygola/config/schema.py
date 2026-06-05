"""Configuration is split into two levels, as discussed:

  - PolicyConfig: the declarative "what is allowed" rules. This is the part a
    compliance officer cares about. It is meant to live in a versioned YAML/JSON
    file and serve as documentation of the policy itself.

  - SetupConfig: the technical "how is it wired" -- which providers, which audit
    backend, which mode. More code-near.

Everything is a Pydantic model so an invalid config fails loudly at startup
rather than silently letting sensitive data through at runtime.

Secure-by-default principle: defaults lean strict. If an entity type is
detected but not explicitly classified, it is treated as blocking.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

import re

from pydantic import BaseModel, Field, field_validator


class Mode(str, Enum):
    """How the pipeline handles forwarding to the commercial LLM."""

    AUTO = "auto"            # sanitize and forward automatically
    CONFIRM = "confirm"      # pause for human confirmation before forwarding


class EntityHandling(str, Enum):
    """What to do when a given entity type is found."""

    PSEUDONYMIZE = "pseudonymize"  # replace with placeholder, restore later
    BLOCK = "block"                # refuse to forward the request at all
    ALLOW = "allow"                # let it through untouched (use sparingly!)


class EntityRule(BaseModel):
    """Policy for one entity type, e.g. EMAIL or IBAN."""

    entity_type: str
    handling: EntityHandling = EntityHandling.PSEUDONYMIZE
    # Minimum detector confidence for this rule to apply (0..1).
    min_score: float = Field(default=0.5, ge=0.0, le=1.0)


class PolicyConfig(BaseModel):
    """The declarative compliance policy -- the auditable artifact."""

    # Entity types we actively look for and how to handle each.
    entity_rules: list[EntityRule] = Field(
        default_factory=lambda: [
            EntityRule(entity_type="EMAIL_ADDRESS", handling=EntityHandling.PSEUDONYMIZE),
            EntityRule(entity_type="PHONE_NUMBER", handling=EntityHandling.PSEUDONYMIZE),
            EntityRule(entity_type="IBAN_CODE", handling=EntityHandling.BLOCK),
            EntityRule(entity_type="CREDIT_CARD", handling=EntityHandling.BLOCK),
            EntityRule(entity_type="PERSON", handling=EntityHandling.PSEUDONYMIZE),
        ]
    )

    # Secure by default: an entity that is detected but has no matching rule is
    # treated according to this fallback. Default is the strict choice.
    unknown_entity_handling: EntityHandling = EntityHandling.BLOCK

    # Whether the (trusted) LLM analysis stage is allowed to ADD entities the
    # deterministic layer missed. It can never override a BLOCK to an ALLOW.
    llm_analysis_enabled: bool = True

    def rule_for(self, entity_type: str) -> EntityRule:
        for rule in self.entity_rules:
            if rule.entity_type == entity_type:
                return rule
        return EntityRule(entity_type=entity_type, handling=self.unknown_entity_handling)


class AuditConfig(BaseModel):
    backend: Literal["jsonfile", "memory"] = "jsonfile"
    # Directory for the jsonfile backend.
    path: str = "./audit_logs"


class ProviderConfig(BaseModel):
    # "mock" lets the whole pipeline run with no API key, for development.
    kind: Literal["mock", "openai", "anthropic", "local"] = "mock"
    model: str = "mock-model"
    # Name of the environment variable that holds the API key.
    # The library reads os.environ[api_key_env] at provider init time.
    # Never put a key value or file path here.
    # Optional for kind="local" — a dummy key is used when absent.
    api_key_env: str = "ANTHROPIC_API_KEY"
    # Base URL for providers that use a configurable endpoint.
    # For kind="local" this defaults to http://localhost:11434/v1 (Ollama).
    base_url: str | None = None
    # Request timeout in seconds passed to the underlying SDK client.
    timeout_seconds: float = 30.0
    # Maximum number of retry attempts for transient errors (rate-limit, timeout).
    # Does NOT apply to ProviderUnavailableError — connection failures are not retried.
    max_retries: int = 3

    @field_validator("api_key_env")
    @classmethod
    def _must_be_env_var_name(cls, v: str) -> str:
        if not re.match(r"^[A-Z_][A-Z0-9_]*$", v, re.IGNORECASE):
            raise ValueError(
                f"api_key_env must be an environment variable name "
                f"(e.g. ANTHROPIC_API_KEY), not a key value or file path. "
                f"Got: {v!r}"
            )
        return v


class SetupConfig(BaseModel):
    """Technical wiring of the pipeline."""

    mode: Mode = Mode.AUTO
    trusted_provider: ProviderConfig = Field(default_factory=ProviderConfig)
    commercial_provider: ProviderConfig = Field(default_factory=ProviderConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)


class GovernanceConfig(BaseModel):
    """Top-level config: setup + policy."""

    setup: SetupConfig = Field(default_factory=SetupConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)

    @field_validator("policy")
    @classmethod
    def _warn_on_allow_all(cls, v: PolicyConfig) -> PolicyConfig:
        # A tiny guardrail: an ALLOW fallback for unknown entities is dangerous
        # and almost certainly a mistake in a compliance tool.
        if v.unknown_entity_handling == EntityHandling.ALLOW:
            raise ValueError(
                "unknown_entity_handling=ALLOW disables secure-by-default "
                "behavior; set it to BLOCK or PSEUDONYMIZE."
            )
        return v

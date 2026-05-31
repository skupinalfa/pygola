"""Tests for secret handling: api_key_env config field and fail-closed behavior.

All key-shaped strings here are clearly invented placeholders.
"""

import pytest

from pygola.config.schema import ProviderConfig
from pygola.factories import build_provider, resolve_api_key
from pygola.providers.base import MockProvider


# ---------------------------------------------------------------------------
# ProviderConfig.api_key_env field
# ---------------------------------------------------------------------------

class TestApiKeyEnvField:
    def test_default_is_anthropic_api_key(self):
        cfg = ProviderConfig(kind="anthropic", model="claude-test-fake")
        assert cfg.api_key_env == "ANTHROPIC_API_KEY"

    def test_custom_var_name_is_stored(self):
        cfg = ProviderConfig(kind="openai", model="gpt-test-fake", api_key_env="MY_OPENAI_KEY")
        assert cfg.api_key_env == "MY_OPENAI_KEY"

    def test_rejects_file_path(self):
        with pytest.raises(ValueError, match="api_key_env must be an environment variable name"):
            ProviderConfig(api_key_env="/tmp/secrets/fake_key.txt")

    def test_rejects_hyphenated_key_shaped_string(self):
        # Hyphens are not valid in env var names — catches accidental raw-key paste.
        with pytest.raises(ValueError, match="api_key_env must be an environment variable name"):
            ProviderConfig(api_key_env="sk-ant-FAKE-INVENTED-VALUE-NOT-REAL")

    def test_rejects_value_with_spaces(self):
        with pytest.raises(ValueError, match="api_key_env must be an environment variable name"):
            ProviderConfig(api_key_env="MY KEY VAR")


# ---------------------------------------------------------------------------
# resolve_api_key — environment reading
# ---------------------------------------------------------------------------

class TestResolveApiKey:
    def test_returns_value_when_var_is_set(self, monkeypatch):
        monkeypatch.setenv("FAKE_PROVIDER_KEY_VAR", "invented-value-aaaaabbbbbccccc")
        cfg = ProviderConfig(kind="anthropic", model="claude-test-fake", api_key_env="FAKE_PROVIDER_KEY_VAR")
        assert resolve_api_key(cfg) == "invented-value-aaaaabbbbbccccc"

    def test_raises_runtime_error_when_var_missing(self, monkeypatch):
        monkeypatch.delenv("FAKE_MISSING_KEY_VAR", raising=False)
        cfg = ProviderConfig(kind="anthropic", model="claude-test-fake", api_key_env="FAKE_MISSING_KEY_VAR")
        with pytest.raises(RuntimeError, match="FAKE_MISSING_KEY_VAR"):
            resolve_api_key(cfg)

    def test_error_message_names_provider_kind(self, monkeypatch):
        monkeypatch.delenv("FAKE_MISSING_KEY_VAR", raising=False)
        cfg = ProviderConfig(kind="openai", model="gpt-test-fake", api_key_env="FAKE_MISSING_KEY_VAR")
        with pytest.raises(RuntimeError, match="openai"):
            resolve_api_key(cfg)


# ---------------------------------------------------------------------------
# build_provider — fail-closed integration
# ---------------------------------------------------------------------------

class TestBuildProviderFailClosed:
    def test_missing_key_raises_runtime_error_not_not_implemented(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cfg = ProviderConfig(kind="anthropic", model="claude-test-fake")
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            build_provider(cfg)

    def test_present_key_does_not_raise_runtime_error(self, monkeypatch):
        # Key is found → fail-closed check passes. The provider is now implemented,
        # so we get either a live provider or an ImportError for the optional SDK —
        # anything but a RuntimeError about a missing environment variable.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "invented-value-aaaaabbbbbccccc")
        cfg = ProviderConfig(kind="anthropic", model="claude-test-fake")
        try:
            build_provider(cfg)
        except RuntimeError as exc:
            pytest.fail(f"Got unexpected RuntimeError (should not be a missing-key error): {exc}")
        except (ImportError, NotImplementedError):
            pass  # SDK not installed or provider stub — both are acceptable here

    def test_custom_env_var_missing_raises_runtime_error(self, monkeypatch):
        monkeypatch.delenv("MY_CUSTOM_LLM_KEY", raising=False)
        cfg = ProviderConfig(kind="openai", model="gpt-test-fake", api_key_env="MY_CUSTOM_LLM_KEY")
        with pytest.raises(RuntimeError, match="MY_CUSTOM_LLM_KEY"):
            build_provider(cfg)


# ---------------------------------------------------------------------------
# Mock provider — no key required
# ---------------------------------------------------------------------------

class TestMockProvider:
    def test_mock_builds_without_any_env_var(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cfg = ProviderConfig(kind="mock")
        provider = build_provider(cfg)
        assert isinstance(provider, MockProvider)

    def test_mock_complete_returns_expected_prefix(self):
        provider = MockProvider()
        result = provider.complete("hello world")
        assert result.startswith("[mock completion]")

    def test_mock_complete_reflects_input_length(self):
        provider = MockProvider()
        text = "x" * 42
        result = provider.complete(text)
        assert "42" in result

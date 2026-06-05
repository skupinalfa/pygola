"""Connector-level error hierarchy.

All errors raised by LLMProvider adapters should be one of these types so the
pipeline can catch them uniformly without importing vendor SDK exceptions.
"""

from __future__ import annotations


class ConnectorError(Exception):
    """Base class for all LLM connector errors."""


class ProviderUnavailableError(ConnectorError):
    """Raised when the provider endpoint cannot be reached.

    This is a configuration / infrastructure problem, not a transient API
    error. The pipeline should not retry on this exception.
    """

    def __init__(self, base_url: str, cause: BaseException | None = None) -> None:
        self.base_url = base_url
        msg = (
            f"LLM provider is unavailable at '{base_url}'. "
            "Check that the server is running and the base_url is correct."
        )
        super().__init__(msg)
        if cause is not None:
            self.__cause__ = cause


class RateLimitError(ConnectorError):
    """Raised when the provider returns a rate-limit response (HTTP 429).

    Safe to retry with exponential back-off.
    """


class TimeoutError(ConnectorError):
    """Raised when a request to the provider exceeds the configured timeout.

    Safe to retry with exponential back-off.
    """

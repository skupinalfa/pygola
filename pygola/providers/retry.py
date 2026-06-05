"""Retry helper for transient LLM connector errors.

Only retries on RateLimitError and TimeoutError. ProviderUnavailableError
(connection refused / server down) is NOT retried — it is a configuration or
infrastructure problem that retrying will not fix.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from .errors import RateLimitError, TimeoutError

T = TypeVar("T")


def retry_with_backoff(
    fn: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
) -> T:
    """Call fn(), retrying on RateLimitError or TimeoutError with exponential back-off.

    Args:
        fn:             Zero-argument callable to call (use functools.partial or a lambda).
        max_retries:    Maximum number of retry attempts after the first failure.
        base_delay:     Seconds to wait before the first retry.
        backoff_factor: Multiplier applied to the delay after each attempt.

    Returns:
        The return value of fn() on success.

    Raises:
        RateLimitError | TimeoutError: after max_retries exhausted.
        Any other exception: propagated immediately without retrying.
    """
    delay = base_delay
    last_exc: RateLimitError | TimeoutError | None = None

    for attempt in range(max_retries + 1):
        try:
            return fn()
        except (RateLimitError, TimeoutError) as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(delay)
                delay *= backoff_factor

    raise last_exc  # type: ignore[misc]

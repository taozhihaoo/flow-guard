"""Retry execution with exponential backoff.

Wraps a fallible step runner and re-executes it according to the step's
:class:`~flowguard.core.models.RetryPolicy` until it succeeds or the
attempt budget is exhausted. Both command failures and timeouts are
retryable; the delay before attempt *N+1* is
``delay * backoff_factor ** (N - 1)``, capped by ``max_delay``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from flowguard.core.models import RetryPolicy, Status, StepResult

__all__ = ["RetryExecutor", "retryable"]

T = TypeVar("T", bound=StepResult)

#: Result statuses that trigger another attempt.
_RETRYABLE_STATUSES = frozenset({Status.FAILED, Status.TIMEOUT})


def retryable(result: StepResult) -> bool:
    """Return ``True`` when *result* represents a retryable outcome."""
    return result.status in _RETRYABLE_STATUSES


class RetryExecutor:
    """Applies a :class:`RetryPolicy` around a step runner.

    ``runner`` is any callable returning a :class:`StepResult` (typically
    :meth:`CommandExecutor.execute` with bound arguments). ``sleep`` is
    injectable so tests can observe backoff timing without waiting.
    """

    def __init__(self, sleep: Callable[[float], None] = time.sleep) -> None:
        self._sleep = sleep

    def run(
        self,
        runner: Callable[[], T],
        policy: RetryPolicy,
        *,
        should_retry: Callable[[StepResult], bool] = retryable,
    ) -> T:
        """Call *runner* until success or the policy's attempts run out.

        The returned result records the total number of attempts made in
        ``attempts``. When every attempt fails, the last result is returned
        as-is (its ``error`` message describes the final failure).
        """
        result = runner()
        result.attempts = 1
        attempt = 1
        while attempt < policy.attempts and should_retry(result):
            delay = policy.delay_for(attempt)
            if delay > 0:
                self._sleep(delay)
            attempt += 1
            result = runner()
            result.attempts = attempt
        return result

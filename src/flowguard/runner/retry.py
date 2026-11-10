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
        with its ``error`` message describing the final failure. Output
        from earlier attempts is preserved as a labelled transcript, and
        ``started_at`` / ``duration_ms`` describe the whole retry sequence
        rather than only the final attempt.
        """
        wall_start = time.monotonic()
        result = runner()
        result.attempts = 1
        first_started_at = result.started_at
        transcripts: list[tuple[int, str, str]] = [(1, result.stdout, result.stderr)]
        attempt = 1
        while attempt < policy.attempts and should_retry(result):
            delay = policy.delay_for(attempt)
            if delay > 0:
                self._sleep(delay)
            attempt += 1
            result = runner()
            result.attempts = attempt
            transcripts.append((attempt, result.stdout, result.stderr))

        if len(transcripts) > 1:
            result.started_at = first_started_at
            result.stdout = _join_transcript(transcripts, field="stdout")
            result.stderr = _join_transcript(transcripts, field="stderr")
            result.duration_ms = (time.monotonic() - wall_start) * 1000.0
        return result


def _join_transcript(transcripts: list[tuple[int, str, str]], *, field: str) -> str:
    """Combine per-attempt output, keeping empty transcripts untouched."""
    index = 1 if field == "stdout" else 2
    if not any(entry[index] for entry in transcripts):
        return ""
    total = len(transcripts)
    return "\n".join(f"--- attempt {entry[0]}/{total} ---\n{entry[index]}" for entry in transcripts)

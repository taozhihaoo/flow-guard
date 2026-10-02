"""Tests for the retry system with exponential backoff (Phase 4)."""

from __future__ import annotations

from collections.abc import Callable

from flowguard.core.models import RetryPolicy, Status, StepResult
from flowguard.runner.retry import RetryExecutor, retryable


def make_result(status: Status, **extra: object) -> StepResult:
    return StepResult(step_name="a", status=status, **extra)  # type: ignore[arg-type]


class RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def run_retry(
    outcomes: list[Status],
    policy: RetryPolicy,
    sleeper: RecordingSleeper | None = None,
) -> StepResult:
    """Retry a runner canned to return *outcomes* in order."""
    outcomes = list(outcomes)
    calls: list[int] = []

    def runner() -> StepResult:
        calls.append(len(calls) + 1)
        return make_result(outcomes.pop(0) if outcomes else Status.SUCCEEDED)

    executor = RetryExecutor(sleep=(sleeper or RecordingSleeper()))
    return executor.run(runner, policy)


class TestRetryBehaviour:
    def test_success_first_attempt_no_retries(self):
        result = run_retry([Status.SUCCEEDED], RetryPolicy(attempts=5))
        assert result.succeeded
        assert result.attempts == 1

    def test_retries_until_success(self):
        result = run_retry(
            [Status.FAILED, Status.FAILED, Status.SUCCEEDED],
            RetryPolicy(attempts=3),
        )
        assert result.succeeded
        assert result.attempts == 3

    def test_attempts_exhausted_returns_last_failure(self):
        result = run_retry([Status.FAILED] * 3, RetryPolicy(attempts=3, delay=0))
        assert not result.succeeded
        assert result.attempts == 3

    def test_no_retry_when_attempts_is_one(self):
        sleeper = RecordingSleeper()
        result = run_retry([Status.FAILED], RetryPolicy(attempts=1), sleeper)
        assert result.attempts == 1
        assert sleeper.delays == []

    def test_timeouts_are_retried(self):
        result = run_retry([Status.TIMEOUT, Status.SUCCEEDED], RetryPolicy(attempts=2))
        assert result.succeeded
        assert result.attempts == 2

    def test_all_timeouts_exhaust_attempts(self):
        result = run_retry([Status.TIMEOUT] * 2, RetryPolicy(attempts=2))
        assert result.status is Status.TIMEOUT
        assert result.attempts == 2

    def test_success_stops_retrying_early(self):
        # 5 attempts budget, succeeds on attempt 2 -> only 2 runs.
        result = run_retry(
            [Status.FAILED, Status.SUCCEEDED, Status.FAILED, Status.FAILED],
            RetryPolicy(attempts=5, delay=0),
        )
        assert result.attempts == 2
        assert result.succeeded


class TestBackoffTiming:
    def test_exponential_delays_observed(self):
        sleeper = RecordingSleeper()
        run_retry(
            [Status.FAILED, Status.FAILED, Status.FAILED],
            RetryPolicy(attempts=4, delay=1, backoff_factor=2),
            sleeper,
        )
        # Delays before attempts 2, 3, 4: 1, 2, 4.
        assert sleeper.delays == [1.0, 2.0, 4.0]

    def test_max_delay_caps_observed_delays(self):
        sleeper = RecordingSleeper()
        run_retry(
            [Status.FAILED] * 3,
            RetryPolicy(attempts=4, delay=1, backoff_factor=10, max_delay=3),
            sleeper,
        )
        assert sleeper.delays == [1.0, 3.0, 3.0]

    def test_zero_delay_never_sleeps(self):
        sleeper = RecordingSleeper()
        run_retry([Status.FAILED] * 2, RetryPolicy(attempts=3), sleeper)
        assert sleeper.delays == []

    def test_real_sleep_honoured_monotonically(self):
        import time

        started = time.monotonic()
        RetryExecutor(sleep=time.sleep).run(
            lambda: make_result(Status.FAILED),
            RetryPolicy(attempts=2, delay=0.15),
        )
        assert time.monotonic() - started >= 0.14


class TestRetryable:
    def test_failed_is_retryable(self):
        assert retryable(make_result(Status.FAILED))

    def test_timeout_is_retryable(self):
        assert retryable(make_result(Status.TIMEOUT))

    def test_success_not_retryable(self):
        assert not retryable(make_result(Status.SUCCEEDED))

    def test_skipped_not_retryable(self):
        assert not retryable(make_result(Status.SKIPPED))


class TestCustomPredicates:
    def test_custom_should_retry_can_veto(self):
        executor = RetryExecutor(sleep=RecordingSleeper())
        calls: list[int] = []

        def runner() -> StepResult:
            calls.append(1)
            return make_result(Status.FAILED)

        result = executor.run(
            runner,
            RetryPolicy(attempts=5),
            should_retry=lambda _result: False,
        )
        assert len(calls) == 1
        assert result.attempts == 1

    def test_retry_only_specific_exit_codes(self):
        def runner_factory(codes: list[int]) -> Callable[[], StepResult]:
            remaining = list(codes)

            def runner() -> StepResult:
                code = remaining.pop(0)
                return make_result(
                    Status.SUCCEEDED if code == 0 else Status.FAILED,
                    exit_code=code,
                )

            return runner

        executor = RetryExecutor(sleep=RecordingSleeper())
        result = executor.run(
            runner_factory([2, 2, 0]),
            RetryPolicy(attempts=5),
            should_retry=lambda r: r.exit_code == 2,
        )
        assert result.succeeded
        assert result.attempts == 3

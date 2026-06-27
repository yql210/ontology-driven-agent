from __future__ import annotations

import time

from ontoagent.execution.circuit_breaker import CircuitBreaker


def test_initial_state_closed():
    cb = CircuitBreaker()
    assert not cb.is_open
    assert cb.state == "closed"


def test_opens_after_threshold_failures():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    assert not cb.is_open  # 2 failures, not yet
    cb.record_failure()
    assert cb.is_open  # 3 failures → open
    assert cb.state == "open"


def test_half_open_after_recovery_timeout():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open
    time.sleep(0.15)
    assert cb.state == "half_open"
    assert not cb.is_open  # half_open allows traffic


def test_success_resets_to_closed():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open
    time.sleep(0.15)
    assert cb.state == "half_open"
    cb.record_success()
    assert cb.state == "closed"
    assert not cb.is_open


def test_record_failure_increments():
    cb = CircuitBreaker(failure_threshold=5)
    assert cb.failure_count == 0
    cb.record_failure()
    assert cb.failure_count == 1
    cb.record_failure()
    assert cb.failure_count == 2

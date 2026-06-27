from __future__ import annotations

from ontoagent.execution.execution_policy import ExecutionPolicy


def test_defaults():
    policy = ExecutionPolicy()
    assert policy.max_retries == 2
    assert policy.retry_delay == 1.0
    assert policy.concurrency_limit == 5
    assert policy.timeout == 60.0


def test_custom_values():
    def my_fallback():
        pass

    policy = ExecutionPolicy(
        max_retries=5,
        retry_delay=0.5,
        concurrency_limit=10,
        timeout=30.0,
        fallback=my_fallback,
    )
    assert policy.max_retries == 5
    assert policy.retry_delay == 0.5
    assert policy.concurrency_limit == 10
    assert policy.timeout == 30.0
    assert policy.fallback is my_fallback


def test_fallback_none_by_default():
    policy = ExecutionPolicy()
    assert policy.fallback is None

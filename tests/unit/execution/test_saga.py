from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ontoagent.execution.action_types import ActionContext, FunctionResult
from ontoagent.execution.saga import SagaExecution, SagaOrchestrator, SagaStep

# =============================================================================
# Helpers
# =============================================================================


def _success_result() -> FunctionResult:
    return FunctionResult(success=True, data={"ok": True})


def _fail_result(msg: str = "step failed") -> FunctionResult:
    return FunctionResult(success=False, error=msg)


def _make_ctx(**overrides) -> ActionContext:
    defaults: dict = {"graph_store": None, "match_data": {}}
    defaults.update(overrides)
    return ActionContext(**defaults)


# =============================================================================
# SagaStep
# =============================================================================


@pytest.mark.unit
def test_saga_step_stores_name_and_callables():
    """SagaStep 正确存储 name、action、compensation。"""
    action = MagicMock(return_value=_success_result())
    compensation = MagicMock(return_value=_success_result())
    step = SagaStep(name="create", action=action, compensation=compensation)
    assert step.name == "create"
    assert step.action is action
    assert step.compensation is compensation


@pytest.mark.unit
def test_saga_step_compensation_default_none():
    """SagaStep compensation 默认为 None。"""
    step = SagaStep(name="read", action=MagicMock())
    assert step.compensation is None


# =============================================================================
# SagaOrchestrator.execute — success path
# =============================================================================


@pytest.mark.unit
def test_saga_all_steps_success():
    """所有步骤成功时返回 success=True，不触发任何补偿。"""
    action_a = MagicMock(return_value=_success_result())
    action_b = MagicMock(return_value=_success_result())
    comp_a = MagicMock(return_value=_success_result())
    comp_b = MagicMock(return_value=_success_result())

    steps = [
        SagaStep(name="a", action=action_a, compensation=comp_a),
        SagaStep(name="b", action=action_b, compensation=comp_b),
    ]

    orchestrator = SagaOrchestrator()
    result = orchestrator.execute(steps, _make_ctx())

    assert result.success is True
    assert len(result.results) == 2
    comp_a.assert_not_called()
    comp_b.assert_not_called()


@pytest.mark.unit
def test_saga_no_compensation_on_success():
    """成功路径不调用任何补偿函数。"""
    comp = MagicMock(return_value=_success_result())
    steps = [
        SagaStep(name="x", action=MagicMock(return_value=_success_result()), compensation=comp),
    ]
    SagaOrchestrator().execute(steps, _make_ctx())
    comp.assert_not_called()


# =============================================================================
# SagaOrchestrator.execute — failure triggers compensation
# =============================================================================


@pytest.mark.unit
def test_saga_step_failure_triggers_compensation():
    """中间步骤失败时触发已完成步骤的补偿（逆序）。"""
    comp_first = MagicMock(return_value=_success_result())

    steps = [
        SagaStep(name="first", action=MagicMock(return_value=_success_result()), compensation=comp_first),
        SagaStep(name="second", action=MagicMock(return_value=_fail_result())),
    ]

    result = SagaOrchestrator().execute(steps, _make_ctx())

    assert result.success is False
    assert "second" in result.error
    comp_first.assert_called_once()


@pytest.mark.unit
def test_saga_exception_triggers_compensation():
    """步骤抛异常时触发补偿并返回失败结果。"""
    comp = MagicMock(return_value=_success_result())

    steps = [
        SagaStep(name="will_succeed", action=MagicMock(return_value=_success_result()), compensation=comp),
        SagaStep(name="will_throw", action=MagicMock(side_effect=RuntimeError("boom"))),
    ]

    result = SagaOrchestrator().execute(steps, _make_ctx())

    assert result.success is False
    assert "boom" in result.error
    comp.assert_called_once()


# =============================================================================
# Compensation edge cases
# =============================================================================


@pytest.mark.unit
def test_saga_compensation_failure_doesnt_stop_others():
    """某个补偿失败不阻断其余补偿。"""
    comp_a = MagicMock(side_effect=RuntimeError("comp-a failed"))
    comp_b = MagicMock(return_value=_success_result())

    steps = [
        SagaStep(name="a", action=MagicMock(return_value=_success_result()), compensation=comp_a),
        SagaStep(name="b", action=MagicMock(return_value=_success_result()), compensation=comp_b),
        SagaStep(name="c", action=MagicMock(return_value=_fail_result())),
    ]

    result = SagaOrchestrator().execute(steps, _make_ctx())

    assert result.success is False
    # Both compensations should have been attempted despite comp_a raising
    comp_a.assert_called_once()
    comp_b.assert_called_once()


@pytest.mark.unit
def test_saga_skip_step_without_compensation():
    """已完成步骤没有 compensation 时跳过（不报错）。"""
    steps = [
        SagaStep(name="no_comp", action=MagicMock(return_value=_success_result()), compensation=None),
        SagaStep(name="fail", action=MagicMock(return_value=_fail_result())),
    ]

    result = SagaOrchestrator().execute(steps, _make_ctx())
    assert result.success is False


# =============================================================================
# State persistence
# =============================================================================


@pytest.mark.unit
def test_saga_persist_state():
    """SAGA 执行过程中通过 graph_store 持久化状态。"""
    mock_store = MagicMock()

    steps = [
        SagaStep(name="a", action=MagicMock(return_value=_success_result())),
        SagaStep(name="b", action=MagicMock(return_value=_success_result())),
    ]

    ctx = _make_ctx(graph_store=mock_store)
    result = SagaOrchestrator().execute(steps, ctx)

    assert result.success is True
    # merge_node should have been called for each status transition
    calls = mock_store.merge_node.call_args_list
    labels = [c[0][0] for c in calls]
    assert all(lbl == "SagaExecution" for lbl in labels)
    # Final call should have status "completed"
    last_props = calls[-1][0][2]
    assert last_props["status"] == "completed"


@pytest.mark.unit
def test_saga_persist_state_on_failure():
    """SAGA 失败时持久化最终状态为 failed。"""
    mock_store = MagicMock()

    steps = [
        SagaStep(name="a", action=MagicMock(return_value=_success_result())),
        SagaStep(name="b", action=MagicMock(return_value=_fail_result())),
    ]

    ctx = _make_ctx(graph_store=mock_store)
    result = SagaOrchestrator().execute(steps, ctx)

    assert result.success is False
    last_props = mock_store.merge_node.call_args_list[-1][0][2]
    assert last_props["status"] == "failed"


# =============================================================================
# SagaExecution unit tests
# =============================================================================


@pytest.mark.unit
def test_saga_execution_initial_state():
    """SagaExecution 初始状态为 pending，completed 为空。"""
    exec = SagaExecution(saga_id="test-id", steps=[])
    assert exec.status == "pending"
    assert exec.completed == []
    assert exec.id == "test-id"


@pytest.mark.unit
def test_saga_execution_persist_without_graph_store():
    """无 graph_store 时 persist 不报错。"""
    exec = SagaExecution(saga_id="x", steps=[])
    exec.persist()  # should not raise

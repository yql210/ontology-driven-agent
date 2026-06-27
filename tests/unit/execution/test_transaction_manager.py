from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ontoagent.execution.transaction_manager import Neo4jTransaction, TransactionManager

# =============================================================================
# TransactionManager tests
# =============================================================================


@pytest.mark.unit
def test_run_atomic_success():
    """run_atomic 在所有操作成功时提交事务并返回结果。"""
    mock_result = MagicMock()
    mock_result.data.return_value = {"n": {"name": "foo"}}

    mock_tx = MagicMock()
    mock_tx.run.return_value = [mock_result]

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.begin_transaction.return_value = mock_tx

    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session

    tm = TransactionManager(mock_driver)
    operations = [
        {"cypher": "CREATE (n:CodeEntity $props)", "params": {"props": {"name": "foo"}}},
    ]
    results = tm.run_atomic(operations)

    assert results == [[{"n": {"name": "foo"}}]]
    mock_tx.commit.assert_called_once()
    mock_tx.rollback.assert_not_called()


@pytest.mark.unit
def test_run_atomic_rollback_on_failure():
    """run_atomic 在操作失败时回滚事务并重新抛出异常。"""
    mock_tx = MagicMock()
    mock_tx.run.side_effect = RuntimeError("db error")

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.begin_transaction.return_value = mock_tx

    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session

    tm = TransactionManager(mock_driver)
    operations = [{"cypher": "BAD QUERY"}]

    with pytest.raises(RuntimeError, match="db error"):
        tm.run_atomic(operations)

    mock_tx.rollback.assert_called_once()
    mock_tx.commit.assert_not_called()


# =============================================================================
# Neo4jTransaction — whitelist validation tests
# =============================================================================


@pytest.mark.unit
def test_neo4j_transaction_create_entity_success():
    """create_entity 对合法 label 正常执行 Cypher。"""
    mock_tx = MagicMock()
    neo4j_tx = Neo4jTransaction(mock_tx)

    neo4j_tx.create_entity("CodeEntity", {"id": "abc", "name": "foo"})

    mock_tx.run.assert_called_once()
    cypher = mock_tx.run.call_args[0][0]
    params = mock_tx.run.call_args[0][1]
    assert ":`CodeEntity`" in cypher
    assert params == {"props": {"id": "abc", "name": "foo"}}


@pytest.mark.unit
def test_neo4j_transaction_create_entity_invalid_label():
    """create_entity 拒绝非法 label。"""
    mock_tx = MagicMock()
    neo4j_tx = Neo4jTransaction(mock_tx)

    with pytest.raises(ValueError, match="Invalid label"):
        neo4j_tx.create_entity("MaliciousLabel", {"id": "abc"})


@pytest.mark.unit
def test_neo4j_transaction_create_relation_success():
    """create_relation 对合法 rel_type 正常执行 Cypher。"""
    mock_tx = MagicMock()
    neo4j_tx = Neo4jTransaction(mock_tx)

    neo4j_tx.create_relation("id-1", "id-2", "calls", {"weight": "0.8"})

    mock_tx.run.assert_called_once()
    cypher = mock_tx.run.call_args[0][0]
    params = mock_tx.run.call_args[0][1]
    assert ":`calls`" in cypher
    assert params["from"] == "id-1"
    assert params["to"] == "id-2"
    assert params["props"] == {"weight": "0.8"}


@pytest.mark.unit
def test_neo4j_transaction_create_relation_without_properties():
    """create_relation 无 properties 时不传 $props。"""
    mock_tx = MagicMock()
    neo4j_tx = Neo4jTransaction(mock_tx)

    neo4j_tx.create_relation("id-1", "id-2", "extends")

    mock_tx.run.assert_called_once()
    cypher = mock_tx.run.call_args[0][0]
    params = mock_tx.run.call_args[0][1]
    assert "$props" not in cypher
    assert params == {"from": "id-1", "to": "id-2"}


@pytest.mark.unit
def test_neo4j_transaction_create_relation_invalid_type():
    """create_relation 拒绝非法 rel_type。"""
    mock_tx = MagicMock()
    neo4j_tx = Neo4jTransaction(mock_tx)

    with pytest.raises(ValueError, match="Invalid relation type"):
        neo4j_tx.create_relation("id-1", "id-2", "hacks_the_planet")


@pytest.mark.unit
def test_neo4j_transaction_allows_saga_execution_label():
    """Neo4jTransaction 允许 SagaExecution 标签（SAGA 持久化用）。"""
    mock_tx = MagicMock()
    neo4j_tx = Neo4jTransaction(mock_tx)

    # 不应抛出异常
    neo4j_tx.create_entity("SagaExecution", {"id": "saga-1", "status": "running"})
    mock_tx.run.assert_called_once()


@pytest.mark.unit
def test_neo4j_transaction_allows_temp_labels():
    """Neo4jTransaction 允许 TempRequest 和 TempDiagnosis 标签。"""
    mock_tx = MagicMock()
    neo4j_tx = Neo4jTransaction(mock_tx)

    neo4j_tx.create_entity("TempRequest", {"id": "req-1"})
    neo4j_tx.create_entity("TempDiagnosis", {"id": "diag-1"})

    assert mock_tx.run.call_count == 2

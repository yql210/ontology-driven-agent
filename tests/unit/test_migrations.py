"""Migration 框架单元测试。"""

import fcntl
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from layerkg.exceptions import SchemaMigrationError
from layerkg.graph_store import GraphStore
from layerkg.migrations import MigrationBase
from layerkg.migrations.registry import MigrationRegistry
from layerkg.migrations.runner import MigrationRunner
from layerkg.schema_version import (
    CURRENT_SCHEMA_VERSION,
)


class DummyMigration(MigrationBase):
    """测试用迁移。"""

    def __init__(self, vfrom: str, vto: str, desc: str = ""):
        self.version_from = vfrom
        self.version_to = vto
        self.description = desc
        self.upgrade_called = False
        self.downgrade_called = False

    def upgrade(self, store: GraphStore) -> None:
        self.upgrade_called = True

    def downgrade(self, store: GraphStore) -> None:
        self.downgrade_called = True


class FailingMigration(DummyMigration):
    """会失败的迁移。"""

    def upgrade(self, store: GraphStore) -> None:
        raise RuntimeError("upgrade failed")

    def downgrade(self, store: GraphStore) -> None:
        raise RuntimeError("downgrade failed")


class TestMigrationBase:
    def test_abstract_methods_required(self):
        with pytest.raises(TypeError):
            MigrationBase()  # type: ignore[abstract]

    def test_dummy_migration_upgrade(self):
        m = DummyMigration("0.0.0", "1.0.0")
        m.upgrade(MagicMock())
        assert m.upgrade_called

    def test_dummy_migration_downgrade(self):
        m = DummyMigration("0.0.0", "1.0.0")
        m.downgrade(MagicMock())
        assert m.downgrade_called


class TestMigrationRegistry:
    def test_register_and_sort(self):
        reg = MigrationRegistry()
        m1 = DummyMigration("0.0.0", "1.0.0")
        m2 = DummyMigration("1.0.0", "1.1.0")
        # 故意反序注册
        reg.register(m2)
        reg.register(m1)
        assert reg.migrations[0].version_to == "1.0.0"
        assert reg.migrations[1].version_to == "1.1.0"

    def test_get_migration_path_single(self):
        reg = MigrationRegistry()
        m = DummyMigration("0.0.0", "1.0.0")
        reg.register(m)
        path = reg.get_migration_path("0.0.0", "1.0.0")
        assert len(path) == 1
        assert path[0].version_to == "1.0.0"

    def test_get_migration_path_chain(self):
        reg = MigrationRegistry()
        m1 = DummyMigration("0.0.0", "1.0.0")
        m2 = DummyMigration("1.0.0", "1.1.0")
        m3 = DummyMigration("1.1.0", "2.0.0")
        reg.register(m3)
        reg.register(m1)
        reg.register(m2)
        path = reg.get_migration_path("0.0.0", "2.0.0")
        assert len(path) == 3
        assert [m.version_to for m in path] == ["1.0.0", "1.1.0", "2.0.0"]

    def test_get_migration_path_partial(self):
        reg = MigrationRegistry()
        m1 = DummyMigration("0.0.0", "1.0.0")
        m2 = DummyMigration("1.0.0", "1.1.0")
        m3 = DummyMigration("1.1.0", "2.0.0")
        reg.register(m1)
        reg.register(m2)
        reg.register(m3)
        path = reg.get_migration_path("0.0.0", "1.1.0")
        assert len(path) == 2
        assert [m.version_to for m in path] == ["1.0.0", "1.1.0"]

    def test_get_migration_path_empty(self):
        reg = MigrationRegistry()
        path = reg.get_migration_path("0.0.0", "1.0.0")
        assert path == []

    def test_get_migration_path_gap(self):
        """不连续的版本链应返回空路径。"""
        reg = MigrationRegistry()
        m1 = DummyMigration("0.0.0", "1.0.0")
        m3 = DummyMigration("1.1.0", "2.0.0")  # gap: no 1.0.0→1.1.0
        reg.register(m1)
        reg.register(m3)
        path = reg.get_migration_path("0.0.0", "2.0.0")
        assert len(path) == 1  # only first step

    def test_get_latest_version(self):
        reg = MigrationRegistry()
        assert reg.get_latest_version() == "0.0.0"
        reg.register(DummyMigration("0.0.0", "1.0.0"))
        assert reg.get_latest_version() == "1.0.0"


def _make_store(version: str | None = None):
    """创建 mock store，模拟 get_current_db_version。"""
    store = MagicMock()
    if version is None:
        store.query.return_value = []
    else:
        store.query.return_value = [{"version": version}]
    return store


class TestMigrationRunnerRunPending:
    def test_no_pending_when_match(self):
        """版本匹配时不执行迁移。"""
        store = _make_store(CURRENT_SCHEMA_VERSION)
        reg = MigrationRegistry()
        runner = MigrationRunner(store, reg)
        with patch.object(runner, "_acquire_lock", return_value=MagicMock()), patch.object(runner, "_release_lock"):
            applied = runner.run_pending()
        assert applied == []

    def test_registers_version_for_empty_db(self):
        """空数据库只注册版本，不执行迁移。"""
        store = _make_store(None)
        reg = MigrationRegistry()
        runner = MigrationRunner(store, reg)
        with patch.object(runner, "_acquire_lock", return_value=MagicMock()), patch.object(runner, "_release_lock"):
            applied = runner.run_pending()
        # 无迁移脚本时只注册版本
        assert applied == []

    def test_runs_single_migration(self):
        """执行单步迁移。"""
        store = _make_store("0.9.0")
        reg = MigrationRegistry()
        m = DummyMigration("0.9.0", "1.0.0")
        reg.register(m)
        # 模拟迁移后版本查询
        store.query.side_effect = [
            [{"version": "0.9.0"}],  # get_current_db_version (check_schema_version)
            [{"version": "0.9.0"}],  # get_current_db_version (run_pending)
            [],  # register_schema_version
        ]
        runner = MigrationRunner(store, reg)
        with patch.object(runner, "_acquire_lock", return_value=MagicMock()), patch.object(runner, "_release_lock"):
            applied = runner.run_pending()
        assert applied == ["1.0.0"]
        assert m.upgrade_called

    def test_fails_on_ahead_version(self):
        """DB 版本领先时抛出异常。"""
        store = _make_store("2.0.0")
        reg = MigrationRegistry()
        runner = MigrationRunner(store, reg)
        with (
            patch.object(runner, "_acquire_lock", return_value=MagicMock()),
            patch.object(runner, "_release_lock"),
            pytest.raises(SchemaMigrationError, match="ahead"),
        ):
            runner.run_pending()

    def test_migration_failure_reports_applied(self):
        """迁移失败时报告已应用的版本。"""
        store = _make_store("0.9.0")
        reg = MigrationRegistry()
        m_ok = DummyMigration("0.9.0", "0.9.5")
        m_fail = FailingMigration("0.9.5", "1.0.0")
        reg.register(m_ok)
        reg.register(m_fail)
        store.query.side_effect = [
            [{"version": "0.9.0"}],  # check
            [{"version": "0.9.0"}],  # run_pending current
            [],  # register after first migration
        ]
        runner = MigrationRunner(store, reg)
        with (
            patch.object(runner, "_acquire_lock", return_value=MagicMock()),
            patch.object(runner, "_release_lock"),
            pytest.raises(SchemaMigrationError, match="Previously applied"),
        ):
            runner.run_pending()


class TestMigrationRunnerRollback:
    def test_rollback_noop_when_same_version(self):
        store = _make_store("1.0.0")
        reg = MigrationRegistry()
        runner = MigrationRunner(store, reg)
        with patch.object(runner, "_acquire_lock", return_value=MagicMock()), patch.object(runner, "_release_lock"):
            result = runner.rollback("1.0.0")
        assert result == []

    def test_rollback_single_step(self):
        store = _make_store("1.0.0")
        reg = MigrationRegistry()
        m = DummyMigration("0.0.0", "1.0.0")
        reg.register(m)
        runner = MigrationRunner(store, reg)
        with patch.object(runner, "_acquire_lock", return_value=MagicMock()), patch.object(runner, "_release_lock"):
            result = runner.rollback("0.0.0")
        assert result == ["0.0.0"]
        assert m.downgrade_called


class TestMigrationRunnerLock:
    def test_lock_prevents_concurrent(self):
        """第二个 runner 应获取锁失败。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "migrate.lock"
            # 手动创建锁
            f1 = open(lock_path, "w")  # noqa: SIM115
            fcntl.flock(f1.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            store = _make_store(None)
            reg = MigrationRegistry()
            runner = MigrationRunner(store, reg, lock_dir=Path(tmpdir))

            with pytest.raises(SchemaMigrationError, match="Another migration"):
                runner._acquire_lock()

            fcntl.flock(f1.fileno(), fcntl.LOCK_UN)
            f1.close()

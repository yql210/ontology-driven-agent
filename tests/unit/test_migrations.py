"""Migration 框架单元测试。"""

import fcntl
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from ontoagent.domain.exceptions import SchemaMigrationError
from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase
from ontoagent.store.migrations.registry import MigrationRegistry
from ontoagent.store.migrations.runner import MigrationRunner
from ontoagent.store.schema_version import (
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
        reg = MigrationRegistry(load_builtins=False)
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
        reg = MigrationRegistry(load_builtins=False)
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
        """内置迁移填补了空隙后，路径应完整返回。"""
        reg = MigrationRegistry()
        m1 = DummyMigration("0.0.0", "1.0.0")
        m3 = DummyMigration("2.0.0", "3.0.0")  # gap 1.0.0→2.0.0 filled by builtin
        reg.register(m1)
        reg.register(m3)
        path = reg.get_migration_path("0.0.0", "3.0.0")
        # m1 + builtins through 2.6.0. The final m3 is not contiguous after v2.6.0.
        assert len(path) == 10

    def test_get_latest_version(self):
        reg = MigrationRegistry()
        # builtin migrations now include v2.6.0
        assert reg.get_latest_version() == "2.6.0"
        reg.register(DummyMigration("2.6.0", "3.0.0"))
        assert reg.get_latest_version() == "3.0.0"


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
        """执行单步迁移（内置迁移链 1.0→1.1→1.2→2.0 也会执行）。"""
        store = _make_store("0.9.0")
        reg = MigrationRegistry()
        m = DummyMigration("0.9.0", "1.0.0")
        reg.register(m)
        # 内置迁移会连锁执行，用 lambda 返回 [] 避免 StopIteration
        version_calls = 0

        def _query_side_effect(*args, **kwargs):
            nonlocal version_calls
            version_calls += 1
            # check_schema_version 和 run_pending 头两次返回版本
            if version_calls <= 2:
                return [{"version": "0.9.0"}]
            return []

        store.query.side_effect = _query_side_effect
        runner = MigrationRunner(store, reg)
        with patch.object(runner, "_acquire_lock", return_value=MagicMock()), patch.object(runner, "_release_lock"):
            applied = runner.run_pending()
        assert "1.0.0" in applied
        assert m.upgrade_called

    def test_registers_each_successful_migration_version(self):
        """Each successful step persists its own target version."""
        store = _make_store("0.9.0")
        reg = MigrationRegistry(load_builtins=False)
        first = DummyMigration("0.9.0", "1.0.0")
        second = DummyMigration("1.0.0", "1.1.0")
        reg.register(first)
        reg.register(second)
        runner = MigrationRunner(store, reg)

        with (
            patch.object(runner, "_acquire_lock", return_value=MagicMock()),
            patch.object(runner, "_release_lock"),
            patch("ontoagent.store.migrations.runner.CURRENT_SCHEMA_VERSION", "1.1.0"),
            patch("ontoagent.store.migrations.runner.register_schema_version") as register_version,
        ):
            applied = runner.run_pending()

        assert applied == ["1.0.0", "1.1.0"]
        assert [call.args[1] for call in register_version.call_args_list] == ["1.0.0", "1.1.0"]

    def test_failed_following_migration_preserves_last_successful_version(self):
        """A failed step leaves the DB registered at the prior successful step."""
        store = _make_store("0.9.0")
        reg = MigrationRegistry(load_builtins=False)
        first = DummyMigration("0.9.0", "1.0.0")
        second = FailingMigration("1.0.0", "1.1.0")
        reg.register(first)
        reg.register(second)
        runner = MigrationRunner(store, reg)

        with (
            patch.object(runner, "_acquire_lock", return_value=MagicMock()),
            patch.object(runner, "_release_lock"),
            patch("ontoagent.store.migrations.runner.CURRENT_SCHEMA_VERSION", "1.1.0"),
            patch("ontoagent.store.migrations.runner.register_schema_version") as register_version,
            pytest.raises(SchemaMigrationError, match=r"Previously applied: \['1.0.0'\]"),
        ):
            runner.run_pending()

        assert [call.args[1] for call in register_version.call_args_list] == ["1.0.0"]

    def test_fails_on_ahead_version(self):
        """DB 版本领先时抛出异常。"""
        store = _make_store("3.0.0")
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

    def test_nebula_rollback_to_zero_deletes_all_schema_version_vertices(self):
        """Nebula rollback deletes every SchemaVersion vertex by concrete VID."""
        store = type("NebulaGraphStore", (), {})()
        id_query = "MATCH (sv:SchemaVersion) RETURN id(sv) AS vid;"

        def query(statement: str, *args: object, **kwargs: object) -> list[dict[str, str]]:
            if statement == id_query:
                return [{"vid": "schema_version_2_3_0"}, {"vid": "schema_version_2_4_0"}]
            if "RETURN" in statement:
                return [{"version": "2.4.0"}]
            return []

        store.query = MagicMock(side_effect=query)
        store.delete_node = MagicMock(return_value=True)
        reg = MigrationRegistry(load_builtins=False)
        migration = DummyMigration("0.0.0", "2.4.0")
        reg.register(migration)
        runner = MigrationRunner(store, reg)

        with patch.object(runner, "_acquire_lock", return_value=MagicMock()), patch.object(runner, "_release_lock"):
            result = runner.rollback("0.0.0")

        assert result == ["0.0.0"]
        assert migration.downgrade_called
        assert store.delete_node.call_args_list == [
            call("schema_version_2_3_0"),
            call("schema_version_2_4_0"),
        ]
        statements = [call.args[0] for call in store.query.call_args_list]
        assert id_query in statements
        assert not any("DELETE VERTEX sv" in statement for statement in statements)

    @pytest.mark.parametrize("vid", ["", None, 42])
    def test_nebula_rollback_to_zero_rejects_malformed_schema_version_vid(self, vid: object):
        """Nebula rollback must fail rather than report success for an invalid VID."""
        store = type("NebulaGraphStore", (), {})()
        store.query = MagicMock(
            side_effect=[
                [{"version": "2.4.0"}],
                [{"vid": vid}],
            ]
        )
        store.delete_node = MagicMock(return_value=True)
        reg = MigrationRegistry(load_builtins=False)
        migration = DummyMigration("0.0.0", "2.4.0")
        reg.register(migration)
        runner = MigrationRunner(store, reg)

        with (
            patch.object(runner, "_acquire_lock", return_value=MagicMock()),
            patch.object(runner, "_release_lock"),
            pytest.raises(RuntimeError, match="SchemaVersion VID"),
        ):
            runner.rollback("0.0.0")

        store.delete_node.assert_not_called()

    def test_rollback_registers_target_version(self):
        """Rollback must persist its target instead of the code's latest version."""
        store = _make_store("2.4.0")
        reg = MigrationRegistry(load_builtins=False)
        migration = DummyMigration("2.3.0", "2.4.0")
        reg.register(migration)
        runner = MigrationRunner(store, reg)

        with patch.object(runner, "_acquire_lock", return_value=MagicMock()), patch.object(runner, "_release_lock"):
            result = runner.rollback("2.3.0")

        assert result == ["2.3.0"]
        assert migration.downgrade_called
        assert store.query.call_args[0][1]["version"] == "2.3.0"


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


@pytest.mark.unit
def test_migration_registry_includes_v5_capability() -> None:
    """Phase 0: migration registry must include v2.0.0 for CapabilityEntity."""
    reg = MigrationRegistry()
    versions = [m.version_to for m in reg.migrations]
    assert "2.0.0" in versions, f"v2.0.0 not found in migration registry: {versions}"


@pytest.mark.unit
def test_current_schema_version_is_2_6_0() -> None:
    """Method graph persistence migration advances the schema to v2.6.0."""
    assert CURRENT_SCHEMA_VERSION == "2.6.0", f"Expected 2.6.0, got {CURRENT_SCHEMA_VERSION}"


@pytest.mark.unit
def test_migration_registry_includes_v2_1_0_module_size() -> None:
    """P1-#1: migration registry must include v2.1.0 for ModuleEntity.size."""
    reg = MigrationRegistry()
    versions = [m.version_to for m in reg.migrations]
    assert "2.1.0" in versions, f"v2.1.0 not found in migration registry: {versions}"


@pytest.mark.unit
def test_v2_1_0_migration_targets_module_entity_size() -> None:
    """P1-#1: v2.1.0 迁移必须从 2.0.0 升级到 2.1.0。"""
    from ontoagent.store.migrations.v2_1_0_add_module_size import ModuleEntitySizeMigration

    migration = ModuleEntitySizeMigration()
    assert migration.version_from == "2.0.0"
    assert migration.version_to == "2.1.0"


@pytest.mark.unit
def test_module_entity_field_names_include_size() -> None:
    """P1-#1: entity_field_names('ModuleEntity') 必须包含 'size'。"""
    from ontoagent.domain.schema import entity_field_names

    fields = entity_field_names("ModuleEntity")
    assert "size" in fields, f"'size' not in ModuleEntity fields: {fields}"


@pytest.mark.unit
def test_capability_entry_identity_migration_updates_nebula_schema_only() -> None:
    """v2.4.0 adds and removes the CapabilityEntity entry identity column in Nebula."""
    from ontoagent.store.migrations.v2_4_0_add_capability_entry_identity import (
        CapabilityEntryIdentityMigration,
    )

    nebula_store = type("NebulaGraphStore", (), {})()
    nebula_store.query = MagicMock()
    neo4j_store = MagicMock()
    migration = CapabilityEntryIdentityMigration()

    migration.upgrade(nebula_store)
    migration.downgrade(nebula_store)
    migration.upgrade(neo4j_store)
    migration.downgrade(neo4j_store)

    assert nebula_store.query.call_args_list == [
        (("ALTER TAG `CapabilityEntity` ADD (`entryCodeEntityId` string);",), {}),
        (("ALTER TAG `CapabilityEntity` DROP (`entryCodeEntityId`);",), {}),
    ]
    neo4j_store.query.assert_not_called()


@pytest.mark.unit
def test_v2_4_0_nebula_upgrade_failure_does_not_register_schema_version() -> None:
    """A failed Nebula DDL must abort before the runner records v2.4.0."""
    from ontoagent.store.migrations.v2_4_0_add_capability_entry_identity import (
        CapabilityEntryIdentityMigration,
    )

    nebula_store = type("NebulaGraphStore", (), {})()
    nebula_store.query = MagicMock(
        side_effect=[
            [{"version": "2.3.0"}],
            [{"version": "2.3.0"}],
            RuntimeError("ALTER TAG failed"),
        ]
    )
    registry = MigrationRegistry(load_builtins=False)
    registry.register(CapabilityEntryIdentityMigration())
    runner = MigrationRunner(nebula_store, registry)

    with (
        patch.object(runner, "_acquire_lock", return_value=MagicMock()),
        patch.object(runner, "_release_lock"),
        pytest.raises(SchemaMigrationError, match=r"2.3.0.*2.4.0"),
    ):
        runner.run_pending()

    assert nebula_store.query.call_count == 3
    assert "ALTER TAG" in nebula_store.query.call_args_list[-1].args[0]


@pytest.mark.unit
def test_migration_registry_includes_capability_entry_identity_version() -> None:
    """v2.4.0 follows multi-repo migration in the built-in upgrade path."""
    registry = MigrationRegistry()

    path = registry.get_migration_path("2.3.0", "2.4.0")

    assert [migration.version_to for migration in path] == ["2.4.0"]
    assert registry.get_latest_version() == "2.6.0"


@pytest.mark.unit
def test_workspace_persistence_migration_is_registered_after_v2_4_0() -> None:
    """The workspace schema migration is the next normal-chain migration."""
    registry = MigrationRegistry()

    path = registry.get_migration_path("2.4.0", CURRENT_SCHEMA_VERSION)

    assert [migration.version_to for migration in path] == ["2.5.0", "2.6.0"]
    assert registry.get_latest_version() == "2.6.0"


@pytest.mark.unit
def test_workspace_persistence_migration_uses_only_dedicated_additive_neo4j_ddl() -> None:
    """Workspace DDL must be named, idempotent, and isolated from legacy graphs."""
    from ontoagent.store.migrations.v2_5_0_workspace_persistence import WorkspacePersistenceMigration

    store = MagicMock()
    migration = WorkspacePersistenceMigration()

    migration.upgrade(store)

    statements = [call.args[0] for call in store.query.call_args_list]
    labels = (
        "OntoAgentWorkspace",
        "OntoAgentWorkspaceBuildTask",
        "OntoAgentWorkspaceGeneration",
        "OntoAgentWorkspaceRepositorySnapshot",
        "OntoAgentWorkspaceActiveBinding",
    )
    assert migration.version_from == "2.4.0"
    assert migration.version_to == "2.5.0"
    assert len(statements) == 6
    assert all("IF NOT EXISTS" in statement for statement in statements)
    assert all(any(label in statement for statement in statements) for label in labels)
    assert all("OntoAgentServiceGraph" not in statement for statement in statements)
    assert all("CodeEntity" not in statement for statement in statements)
    assert all("DELETE" not in statement and "DROP" not in statement for statement in statements)


@pytest.mark.unit
def test_workspace_persistence_migration_runner_is_idempotent() -> None:
    """A rerun after recording v2.5.0 must not replay workspace DDL."""

    class VersionTrackingStore:
        def __init__(self) -> None:
            self.version = "2.4.0"
            self.statements: list[str] = []

        def query(self, statement: str, params: dict[str, object] | None = None) -> list[dict[str, str]]:
            self.statements.append(statement)
            if "RETURN sv.version AS version" in statement:
                return [{"version": self.version}]
            if "MERGE (sv:SchemaVersion" in statement:
                assert params is not None
                self.version = str(params["version"])
            return []

    store = VersionTrackingStore()
    runner = MigrationRunner(store, MigrationRegistry(load_builtins=True))  # type: ignore[arg-type]

    with patch.object(runner, "_acquire_lock", return_value=MagicMock()), patch.object(runner, "_release_lock"):
        assert runner.run_pending() == ["2.5.0", "2.6.0"]
        assert runner.run_pending() == []

    ddl = [statement for statement in store.statements if statement.startswith("CREATE CONSTRAINT")]
    assert len(ddl) == 13


@pytest.mark.unit
def test_method_graph_migration_is_additive_and_isolated_from_endpoint_and_manifest_labels() -> None:
    """v2.6.0 owns only the dedicated method writer labels."""
    from ontoagent.store.migrations.v2_6_0_method_graph import MethodGraphMigration

    store = MagicMock()
    migration = MethodGraphMigration()

    migration.upgrade(store)

    statements = [call.args[0] for call in store.query.call_args_list]
    assert migration.version_from == "2.5.0"
    assert migration.version_to == "2.6.0"
    assert len(statements) == 7
    assert all(
        "IF NOT EXISTS" in statement and "(n.namespace, n.id) IS UNIQUE" in statement for statement in statements
    )
    assert all("Endpoint" not in statement and "OntoAgentServiceGraph" not in statement for statement in statements)

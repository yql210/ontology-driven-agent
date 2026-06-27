"""Schema 版本追踪单元测试。"""

from unittest.mock import MagicMock

from layerkg.domain.exceptions import SchemaMigrationError
from layerkg.store.schema_version import (
    CURRENT_SCHEMA_VERSION,
    SchemaStatus,
    check_schema_version,
    get_current_db_version,
    register_schema_version,
)


class TestSchemaStatus:
    def test_status_values(self):
        assert SchemaStatus.EMPTY.value == "empty"
        assert SchemaStatus.MATCH.value == "match"
        assert SchemaStatus.BEHIND.value == "behind"
        assert SchemaStatus.AHEAD.value == "ahead"


class TestRegisterSchemaVersion:
    def test_register_calls_query(self):
        store = MagicMock()
        store.query.return_value = []
        register_schema_version(store)
        assert store.query.call_count == 1
        call_args = store.query.call_args
        assert "MERGE" in call_args[0][0]
        assert call_args[0][1]["version"] == CURRENT_SCHEMA_VERSION

    def test_register_includes_description(self):
        store = MagicMock()
        store.query.return_value = []
        register_schema_version(store)
        call_args = store.query.call_args
        assert "description" in call_args[0][1]
        assert "applied_at" in call_args[0][1]

    def test_register_idempotent(self):
        store = MagicMock()
        store.query.return_value = []
        register_schema_version(store)
        register_schema_version(store)
        assert store.query.call_count == 2  # MERGE is idempotent


class TestGetCurrentDbVersion:
    def test_returns_version_when_exists(self):
        store = MagicMock()
        store.query.return_value = [{"version": "1.0.0"}]
        assert get_current_db_version(store) == "1.0.0"

    def test_returns_none_when_empty(self):
        store = MagicMock()
        store.query.return_value = []
        assert get_current_db_version(store) is None

    def test_returns_latest_version(self):
        store = MagicMock()
        store.query.return_value = [{"version": "1.1.0"}]
        assert get_current_db_version(store) == "1.1.0"


class TestCheckSchemaVersion:
    def test_empty_db(self):
        store = MagicMock()
        store.query.return_value = []
        assert check_schema_version(store) == SchemaStatus.EMPTY

    def test_matching_version(self):
        store = MagicMock()
        store.query.return_value = [{"version": CURRENT_SCHEMA_VERSION}]
        assert check_schema_version(store) == SchemaStatus.MATCH

    def test_behind_version(self):
        store = MagicMock()
        store.query.return_value = [{"version": "0.9.0"}]
        assert check_schema_version(store) == SchemaStatus.BEHIND

    def test_ahead_version(self):
        store = MagicMock()
        store.query.return_value = [{"version": "2.0.0"}]
        assert check_schema_version(store) == SchemaStatus.AHEAD


class TestSchemaMigrationError:
    def test_is_layerkg_error(self):
        from layerkg.domain.exceptions import LayerKGError

        err = SchemaMigrationError("test")
        assert isinstance(err, LayerKGError)

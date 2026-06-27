TDD Task 1: CodeEntity 加 6 个业务入口字段

文件: src/ontoagent/domain/schema.py

1. 在 CodeEntity 的 created_at 字段之后、VALID_ENTITY_TYPES 常量之前追加 6 个字段:
   entry_category, entry_metadata, business_process, business_priority, business_lifecycle, business_owner
   全部类型 str | None = None

2. 在 VALID_ENTITY_TYPES 附近追加类级常量（非 dataclass 字段）:
   VALID_ENTRY_CATEGORIES = frozenset({"http_api", "rpc_service", "scheduled", "mq_consumer", "event_handler"})

3. 在 __post_init__ 末尾追加校验:
   检查 entry_category 在 VALID_ENTRY_CATEGORIES 内或为 None

跑: pytest tests/unit/test_schema.py tests/unit/test_schema_extra.py -v
预期: 全部通过

git add src/ontoagent/domain/schema.py
git commit -m "feat(schema): add 6 business entry fields to CodeEntity"
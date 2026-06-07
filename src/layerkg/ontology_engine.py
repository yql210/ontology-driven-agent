"""Ontology Action/Function 引擎 — Palantir 式本体行为层。

实现 Action 绑定实体类型、Function 挂载在 Action 下、Agent 选择 Function 的三层架构。
"""

from __future__ import annotations

import importlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

logger = logging.getLogger(__name__)


# --- 数据结构 ---


@dataclass
class FunctionDef:
    """Function 定义 — 具体执行策略。"""

    name: str
    description: str
    implementation: str  # "module.path:function_name"

    def resolve(self) -> Callable:
        """延迟加载：首次调用时动态导入并缓存。"""
        module_path, func_name = self.implementation.rsplit(":", 1)
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)
        self.__dict__["_resolved"] = func
        return func

    @property
    def callable(self) -> Callable:
        """返回已解析的可调用对象，首次访问时触发 resolve。"""
        if "_resolved" not in self.__dict__:
            return self.resolve()
        return self.__dict__["_resolved"]


@dataclass
class ActionDef:
    """Action 定义 — 操作的抽象接口。"""

    name: str
    description: str
    bind_to: str  # 绑定的实体类型名
    requires_approval: bool = False
    functions: list[FunctionDef] = field(default_factory=list)


@dataclass
class ActionResult:
    """Action 执行结果。"""

    success: bool
    function_name: str  # 实际执行的 Function
    result: Any
    side_effects: list[str]  # 副作用记录
    audit_id: str  # 审计日志 ID


# --- 协议（解耦 GraphStore 依赖） ---


class GraphStoreProtocol(Protocol):
    """GraphStore 最小接口协议，用于 Action Function 的只读访问。"""

    def get_node(self, node_id: str) -> dict | None: ...
    def query(self, cypher: str, params: dict | None = None) -> list[dict]: ...


# --- 组件 ---


class ActionResolver:
    """Action 解析器 — entity_id -> entity_type -> ActionDef。"""

    def __init__(self, graph_store: GraphStoreProtocol) -> None:
        self._store = graph_store

    def resolve_entity_type(self, entity_id: str) -> str:
        """通过 graph_store 查询实体类型。

        Args:
            entity_id: 实体 ID。

        Returns:
            实体类型名称（如 "code_entity"）。

        Raises:
            ValueError: 实体不存在或无类型标签。
        """
        node = self._store.get_node(entity_id)
        if node is None:
            raise ValueError(f"Entity not found: {entity_id}")

        labels = node.get("labels", [])
        if isinstance(labels, str):
            labels = [labels]

        # 映射 Neo4j 标签到实体类型键
        label_to_key: dict[str, str] = {
            "CodeEntity": "code_entity",
            "ConceptEntity": "concept_entity",
            "DocEntity": "doc_entity",
            "ResourceEntity": "resource_entity",
            "ModuleEntity": "module_entity",
            "ChangeSetEntity": "changeset_entity",
        }
        for label in labels:
            key = label_to_key.get(label)
            if key is not None:
                return key

        # 降级：从 entityType 字段推断
        entity_type = node.get("entityType") or node.get("entity_type")
        if entity_type:
            return entity_type.lower()

        raise ValueError(f"Cannot determine entity type for: {entity_id}")

    def resolve_action(
        self,
        entity_type: str,
        action_name: str,
        registry: dict[str, dict[str, ActionDef]],
    ) -> ActionDef:
        """查找实体类型对应的 Action。

        Args:
            entity_type: 实体类型键。
            action_name: Action 名称。
            registry: Action 注册表。

        Raises:
            ValueError: Action 未注册。
        """
        actions = registry.get(entity_type, {})
        action = actions.get(action_name)
        if action is None:
            raise ValueError(f"Action '{action_name}' not found for entity type '{entity_type}'")
        return action


class FunctionSelector:
    """Function 选择器 — Phase 1 用规则匹配。"""

    def select(self, functions: list[FunctionDef], context: dict) -> FunctionDef:
        """根据 context 选择最合适的 Function。

        Phase 1 规则匹配逻辑：
        - context 中有 "reason": "lines > 100" -> 选 split_large_function
        - context 中有 "alert_type": "error_spike" -> 选 analyze_by_log_pattern
        - context 中有 "lines" 且 > 100 -> 选 split_large_function
        - 默认选第一个 Function

        Args:
            functions: 候选 Function 列表。
            context: 场景上下文。

        Returns:
            选中的 FunctionDef。
        """
        if not functions:
            raise ValueError("No functions available to select from")

        func_map = {f.name: f for f in functions}

        # 规则 1：context 中指定了 function_name
        fn_name = context.get("function_name")
        if fn_name and fn_name in func_map:
            return func_map[fn_name]

        # 规则 2：基于 reason 匹配
        reason = context.get("reason", "")
        if ("lines" in str(reason) or "large" in str(reason)) and "split_large_function" in func_map:
            return func_map["split_large_function"]

        # 规则 3：基于 lines 数值
        lines = context.get("lines", 0)
        if isinstance(lines, int) and lines > 100 and "split_large_function" in func_map:
            return func_map["split_large_function"]

        # 默认：选第一个
        return functions[0]


class AuditLogger:
    """审计日志 — 记录所有 Action 执行。"""

    def __init__(self) -> None:
        self._logs: list[dict] = []

    def log_execution(
        self,
        entity_id: str,
        action_name: str,
        function_name: str,
        context: dict,
        result: ActionResult,
    ) -> str:
        """记录执行日志，返回 audit_id。

        Args:
            entity_id: 目标实体 ID。
            action_name: Action 名称。
            function_name: 实际执行的 Function 名称。
            context: 执行上下文。
            result: 执行结果。

        Returns:
            audit_id 字符串。
        """
        audit_id = f"audit-{uuid.uuid4().hex[:12]}"
        entry = {
            "audit_id": audit_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "entity_id": entity_id,
            "action_name": action_name,
            "function_name": function_name,
            "context": context,
            "success": result.success,
            "side_effects": result.side_effects,
        }
        self._logs.append(entry)
        logger.info("Action executed: %s.%s via %s (audit_id=%s)", entity_id, action_name, function_name, audit_id)
        return audit_id

    @property
    def logs(self) -> list[dict]:
        """返回所有审计日志条目。"""
        return list(self._logs)


class OntologyEngine:
    """本体引擎 — 组合 ActionResolver/FunctionSelector/AuditLogger，对外提供统一接口。"""

    def __init__(self, graph_store: GraphStoreProtocol) -> None:
        self._actions: dict[str, dict[str, ActionDef]] = {}
        self._resolver = ActionResolver(graph_store)
        self._selector = FunctionSelector()
        self._audit = AuditLogger()

    def load_from_yaml(self, path: Path) -> None:
        """从 YAML 动态加载 Action 注册表。

        Args:
            path: YAML 配置文件路径。

        Raises:
            FileNotFoundError: 文件不存在。
            ValueError: YAML 格式错误。
        """
        if not path.exists():
            raise FileNotFoundError(f"YAML config not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError("YAML root must be a mapping")

        for entity_type, entity_data in data.items():
            actions_data = entity_data.get("actions", [])
            action_map: dict[str, ActionDef] = {}
            for action_data in actions_data:
                functions = [
                    FunctionDef(
                        name=fn["name"],
                        description=fn.get("description", ""),
                        implementation=fn["implementation"],
                    )
                    for fn in action_data.get("functions", [])
                ]
                action_def = ActionDef(
                    name=action_data["name"],
                    description=action_data.get("description", ""),
                    bind_to=entity_type,
                    requires_approval=action_data.get("requires_approval", False),
                    functions=functions,
                )
                action_map[action_def.name] = action_def
            self._actions[entity_type] = action_map

    def get_actions(self, entity_type: str) -> list[ActionDef]:
        """查询某个实体类型有哪些 Action。

        Args:
            entity_type: 实体类型键（如 "code_entity"）。

        Returns:
            ActionDef 列表。
        """
        return list(self._actions.get(entity_type, {}).values())

    def get_functions(self, entity_type: str, action_name: str) -> list[FunctionDef]:
        """查询某个 Action 下有哪些 Function。

        Args:
            entity_type: 实体类型键。
            action_name: Action 名称。

        Returns:
            FunctionDef 列表。
        """
        actions = self._actions.get(entity_type, {})
        action = actions.get(action_name)
        if action is None:
            return []
        return action.functions

    def execute(self, entity_id: str, action_name: str, context: dict) -> ActionResult:
        """执行 Action 的完整流程。

        流程：
        1. resolver 解析 entity_id -> entity_type
        2. resolver 查找 ActionDef
        3. selector 从 Action 的 functions 中选择
        4. 执行 Function
        5. audit 记录日志

        Args:
            entity_id: 目标实体 ID。
            action_name: 要执行的 Action 名称。
            context: 场景上下文。

        Returns:
            ActionResult 执行结果。

        Raises:
            ValueError: 实体不存在或 Action 未注册。
        """
        # Step 1: 解析 entity_type
        entity_type = self._resolver.resolve_entity_type(entity_id)

        # Step 2: 查找 ActionDef
        action = self._resolver.resolve_action(entity_type, action_name, self._actions)

        # Step 3: 选择 Function
        selected_fn = self._selector.select(action.functions, context)

        # Step 4: 执行 Function
        graph_store = self._resolver._store
        try:
            fn_result = selected_fn.callable(entity_id=entity_id, context=context, graph_store=graph_store)
            success = True
            result_data = fn_result if isinstance(fn_result, dict) else {"value": fn_result}
            side_effects = result_data.get("side_effects", [])
        except Exception as e:
            success = False
            result_data = {"error": str(e)}
            side_effects = []
            logger.exception("Function %s failed for entity %s", selected_fn.name, entity_id)

        # Step 5: 构造结果并记录审计
        action_result = ActionResult(
            success=success,
            function_name=selected_fn.name,
            result=result_data,
            side_effects=side_effects,
            audit_id="",  # 先占位，log_execution 会赋值
        )
        audit_id = self._audit.log_execution(
            entity_id=entity_id,
            action_name=action_name,
            function_name=selected_fn.name,
            context=context,
            result=action_result,
        )
        action_result.audit_id = audit_id

        return action_result

    @property
    def audit_logger(self) -> AuditLogger:
        """暴露审计日志器供测试访问。"""
        return self._audit

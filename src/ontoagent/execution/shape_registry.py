"""Shape registry — 加载、校验、按 (resource_type, operation) 倒排索引。"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import yaml

from ontoagent.domain.shapes import ConstraintShape, Operation

logger = logging.getLogger(__name__)


class ShapeRegistry:
    """约束 Shape 注册表。

    维护 (resource_type, operation) → list[ConstraintShape] 的倒排索引，
    支持 O(1) 哈希查找匹配的 Shape 列表，按 priority 降序返回。

    倒排索引: ``dict[tuple[str, Operation], list[ConstraintShape]]``。
    """

    def __init__(self, valid_labels: set[str]) -> None:
        """初始化注册表。

        Args:
            valid_labels: 合法的 Neo4j 实体标签集合（如 {"CodeEntity", "DataAsset"}）。
                validate_shape 据此校验 target.resource_type 与 path.target_label。
        """
        self._valid_labels: set[str] = set(valid_labels)
        self._shapes: dict[str, ConstraintShape] = {}
        self._index: dict[tuple[str, Operation], list[ConstraintShape]] = defaultdict(list)

    # ------------------------------------------------------------------
    # 注册 / 加载
    # ------------------------------------------------------------------

    def register(self, shape: ConstraintShape) -> None:
        """注册单条 Shape 到倒排索引。

        Args:
            shape: 待注册的 ConstraintShape。

        Raises:
            ValueError: 当 shape.id 已存在，或 validate_shape 失败时。
        """
        if shape.id in self._shapes:
            raise ValueError(f"Shape {shape.id!r} already registered")
        self.validate_shape(shape)
        self._shapes[shape.id] = shape
        key = (shape.target.resource_type, shape.target.operation)
        self._index[key].append(shape)
        logger.debug("Registered shape %s → %s", shape.id, key)

    def load_from_yaml(self, path: Path) -> None:
        """从 shapes.yaml 批量加载 Shape（原子语义：失败时不影响已有状态）。

        流程: 读 YAML → 遍历 shapes 列表 → 每条 from_yaml_dict → validate → 加入倒排索引。
        所有解析/校验错误被聚合后一次性抛出。

        Args:
            path: YAML 文件路径。

        Raises:
            FileNotFoundError: 文件不存在时。
            ValueError: 文件结构非法或某条 Shape 解析/校验失败时（聚合所有错误）。
        """
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        shapes_data = data.get("shapes") or []
        if not isinstance(shapes_data, list):
            raise ValueError(f"YAML 'shapes' 字段必须是列表，实际: {type(shapes_data).__name__}")

        parsed: list[ConstraintShape] = []
        errors: list[str] = []
        for i, shape_data in enumerate(shapes_data):
            try:
                shape = ConstraintShape.from_yaml_dict(shape_data)
            except (ValueError, KeyError) as exc:
                errors.append(f"  [{i}] 解析失败: {exc}")
                continue
            try:
                self.validate_shape(shape)
            except ValueError as exc:
                errors.append(f"  [{i}] {shape.id}: {exc}")
                continue
            if shape.id in self._shapes:
                errors.append(f"  [{i}] {shape.id}: 已在注册表中存在")
                continue
            parsed.append(shape)

        if errors:
            raise ValueError(f"加载 {path} 失败，共 {len(errors)} 条错误:\n" + "\n".join(errors))

        for shape in parsed:
            key = (shape.target.resource_type, shape.target.operation)
            self._shapes[shape.id] = shape
            self._index[key].append(shape)

        logger.info("Loaded %d shapes from %s", len(parsed), path)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_shapes(self, resource_type: str, operation: Operation) -> list[ConstraintShape]:
        """按 (resource_type, operation) 查询匹配的 Shape 列表。

        O(1) 哈希查找倒排索引，结果按 priority 降序排列。
        disabled 的 Shape 不返回。

        Args:
            resource_type: 实体标签（PascalCase）。
            operation: 操作类型。

        Returns:
            按 priority 降序、enabled 的 Shape 列表（副本）。
        """
        key = (resource_type, operation)
        candidates = [s for s in self._index.get(key, []) if s.enabled]
        return sorted(candidates, key=lambda s: s.priority, reverse=True)

    def all_shapes(self) -> list[ConstraintShape]:
        """返回所有已注册的 Shape（按 id 排序的副本）。"""
        return [self._shapes[k] for k in sorted(self._shapes.keys())]

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    def validate_shape(self, shape: ConstraintShape) -> None:
        """校验单条 Shape 的合法性，批量报告所有错误。

        校验项:
            - target.resource_type 必须在 valid_labels 中。
            - 若 path 非 SELF，path.target_label 必须在 valid_labels 中。

        Args:
            shape: 待校验的 Shape。

        Raises:
            ValueError: 当任一校验失败时，聚合所有错误消息后抛出。
        """
        errors: list[str] = []

        if shape.target.resource_type not in self._valid_labels:
            errors.append(f"target.resource_type {shape.target.resource_type!r} 不在合法标签集合中")

        if not shape.path.is_self() and shape.path.target_label and shape.path.target_label not in self._valid_labels:
            errors.append(f"path.target_label {shape.path.target_label!r} 不在合法标签集合中")

        if errors:
            raise ValueError(f"Shape {shape.id!r} 校验失败: " + "; ".join(errors))

    # ------------------------------------------------------------------
    # 其他
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """清空注册表（用于测试）。"""
        self._shapes.clear()
        self._index.clear()

    @property
    def valid_labels(self) -> set[str]:
        """返回合法标签集合（只读副本）。"""
        return set(self._valid_labels)

    def __len__(self) -> int:
        return len(self._shapes)

    def __contains__(self, shape_id: object) -> bool:
        return shape_id in self._shapes

"""Cypher → nGQL best-effort 自动转换器。

NebulaGraph 与 Neo4j 的 Cypher 子集存在若干差异（labels/tags、属性访问前缀、
inline property matching、单等号、startNode/endNode 等）。本模块提供
:class:`CypherToNgqlAdapter`，在 :class:`ontoagent.store.nebula_store.NebulaGraphStore`
内部调用，自动将 OntoAgent 仓库中常见的高频 Cypher 模式翻译成 nGQL。

设计原则：
- **best-effort**：无法识别/转换的查询原样返回，仅 ``logging.warning`` 记录。
- **不动 Neo4j 路径**：仅在 NebulaGraphStore 中调用；Neo4jStore 直接传 Cypher。
- **不处理 MATCH path = ... / SET 写操作 / MERGE**：这些需要上层代码改造。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


class CypherToNgqlAdapter:
    """将 OntoAgent 内部使用的 Cypher 子集自动转换为 nGQL。

    处理规则（按调用顺序）：
        1. ``labels(n)`` → ``tags(n)``（含 ``size(labels(n))`` / ``labels(n)[0]``）
        2. 从 ``MATCH (var:Tag)`` 提取 ``var → Tag`` 映射
        3. inline property matching ``(n:Tag {prop: $val})`` → ``(n:Tag) WHERE n.Tag.prop == $val``
        4. ``n.field`` → ``n.Tag.field``（已有 Tag 前缀的不动）
        5. ``n.id`` → ``id(n)``（ NebulaGraph 中 id 是函数不是属性）
        6. WHERE 中的单 ``=`` → ``==``（``SET =`` / ``>=`` / ``<=`` / ``!=`` / ``+=`` 不动）
        7. ``startNode(r).id`` → ``id(a)`` / ``src(r)``，``endNode(r).id`` → ``id(b)`` / ``dst(r)``
        8. 保留字属性名加反引号

    不处理：``MATCH path = ...`` + ``length(path)`` / ``nodes(p)``、``SET`` 写操作、``MERGE``。
    """

    #: nGQL 中的保留字属性名（命中则加反引号）
    NGQL_RESERVED: frozenset[str] = frozenset(
        {"tag", "edge", "vertex", "step", "path", "source", "target", "rank", "type", "labels"}
    )

    #: WHERE 子句的结束关键字（用于定位 WHERE body 边界）
    _WHERE_TERMINATORS: frozenset[str] = frozenset(
        {"RETURN", "WITH", "ORDER", "LIMIT", "SKIP", "UNION", "SET", "REMOVE", "DELETE"}
    )

    # ---------- public API ----------

    def adapt(self, cypher: str, params: dict | None = None) -> str:
        """转换 Cypher → nGQL（best-effort）。

        Args:
            cypher: 待转换的 Cypher 语句。
            params: 调用方传入的参数字典（仅占位，转换逻辑不使用）。

        Returns:
            转换后的 nGQL 语句。无法转换时返回原始输入并记录 ``warning``。
        """
        if not cypher or not cypher.strip():
            return cypher

        try:
            var_tag_map = self._build_var_tag_map(cypher)
            result = cypher
            result = self._fix_inline_matching(result, var_tag_map)
            result = self._fix_labels(result)
            result = self._fix_property_access(result, var_tag_map)
            result = self._fix_equality(result)
            result = self._fix_start_end_node(result)
            result = self._fix_reserved_words(result)
        except Exception as exc:  # pragma: no cover - defensive, best-effort
            logger.warning(
                "CypherToNgqlAdapter transformation failed (returning original): %s | cypher=%r",
                exc,
                cypher,
            )
            return cypher

        if result != cypher:
            logger.debug("[CypherAdapter] transformed:\n  in:  %s\n  out: %s", cypher, result)
        return result

    # ---------- 规则实现 ----------

    def _build_var_tag_map(self, cypher: str) -> dict[str, str]:
        """从 ``MATCH (var:Tag)`` 提取 ``{var: Tag}`` 映射。

        匹配 ``(var:Tag)`` / ``(var:Tag1:Tag2)`` / ``(var:Tag {x: $y})`` 等形式。
        匿名 ``(n)`` 或 ``(:Tag)`` 不在结果中。

        Args:
            cypher: Cypher 语句。

        Returns:
            ``{var: Tag}`` 字典（多标签取第一个）；可能为空。
        """
        var_tag_map: dict[str, str] = {}
        # 匹配 (var:Tag 形式（开括号 + 标识符 + 冒号 + Tag名）
        # 不要求闭括号，因为后面可能跟 {inline} 或 -[r]-> 等
        for match in re.finditer(r"\(([A-Za-z_]\w*):([A-Za-z_]\w*)", cypher):
            var, tag = match.group(1), match.group(2)
            # 保留第一次出现的 Tag（多标签 Tag1:Tag2 取第一个）
            var_tag_map.setdefault(var, tag)
        return var_tag_map

    def _fix_labels(self, cypher: str) -> str:
        """``labels(...)`` → ``tags(...)`` 字符串替换。

        覆盖 ``labels(n)`` / ``size(labels(n))`` / ``labels(n)[0]``。
        """
        return re.sub(r"\blabels\(", "tags(", cypher)

    def _fix_inline_matching(self, cypher: str, var_tag_map: dict[str, str]) -> str:
        """``(n:Tag {prop: $val, ...})`` → ``(n:Tag)`` + ``WHERE n.Tag.prop == $val AND ...``。

        若原语句已有 WHERE，则将新条件 AND 到 WHERE 开头；否则在合适位置插入 WHERE。
        ``params`` 中无需提前替换 ``$val``，因为 ``$val`` 在 nGQL 中也是参数占位符。

        Args:
            cypher: Cypher 语句。
            var_tag_map: ``{var: Tag}`` 映射（用于补全属性前缀）。

        Returns:
            转换后的语句；无可转换的 inline pattern 时返回原语句。
        """
        inline_pattern = re.compile(r"\(([A-Za-z_]\w*):([A-Za-z_]\w*)\s*\{([^}]*)\}\)")
        matches = list(inline_pattern.finditer(cypher))
        if not matches:
            return cypher

        extra_conditions: list[str] = []
        new_cypher = cypher
        # 倒序替换，避免索引位移
        for match in reversed(matches):
            var, tag, props_str = match.group(1), match.group(2), match.group(3)
            conditions = self._parse_inline_props(props_str, var, tag)
            extra_conditions.extend(conditions)
            replacement = f"({var}:{tag})"
            new_cypher = new_cypher[: match.start()] + replacement + new_cypher[match.end() :]

        if not extra_conditions:
            return new_cypher

        extra_clause = " AND ".join(extra_conditions)

        # 若已存在 WHERE，AND 进去（插在 WHERE 关键字之后）
        if re.search(r"\bWHERE\b", new_cypher):
            return re.sub(
                r"\bWHERE\b",
                f"WHERE {extra_clause} AND",
                new_cypher,
                count=1,
            )

        # 否则：在第一个终结关键字之前插入 WHERE
        terminator_pattern = re.compile(
            r"\b(" + "|".join(sorted(self._WHERE_TERMINATORS)) + r")\b",
            re.IGNORECASE,
        )
        terminator = terminator_pattern.search(new_cypher)
        if terminator:
            insert_pos = terminator.start()
            return new_cypher[:insert_pos] + f"WHERE {extra_clause} " + new_cypher[insert_pos:]

        # 兜底：尾部追加
        return new_cypher.rstrip().rstrip(";") + f" WHERE {extra_clause};"

    def _parse_inline_props(self, props_str: str, var: str, tag: str) -> list[str]:
        """解析 inline property 字符串为 ``var.Tag.prop == value`` 条件列表。

        Args:
            props_str: ``name: $val, age: $other`` 形式的字符串。
            var: 变量名（如 ``n``）。
            tag: Tag 名（如 ``CodeEntity``）。

        Returns:
            条件字符串列表。
        """
        if not props_str.strip():
            return []
        conditions: list[str] = []
        # 简单按逗号切分；嵌套 map 罕见
        for part in _split_top_level(props_str, ","):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            key = key.strip().strip("`")
            value = value.strip()
            if not key or not value:
                continue
            conditions.append(f"{var}.{tag}.{key} == {value}")
        return conditions

    def _fix_property_access(self, cypher: str, var_tag_map: dict[str, str]) -> str:
        """``n.field`` → ``n.Tag.field``；``n.id`` → ``id(n)``。

        - 已有 ``n.Tag.`` 前缀的不动。
        - ``n.id`` 优先转成 ``id(n)``（NebulaGraph 中 id 是函数不是属性）。

        Args:
            cypher: Cypher 语句。
            var_tag_map: ``{var: Tag}`` 映射。

        Returns:
            转换后的语句。
        """
        result = cypher
        for var, tag in var_tag_map.items():
            # 1) n.id → id(n)（但 n.id(  ) 函数调用形式不动，虽然 n.id() 不是合法 Cypher）
            result = re.sub(
                rf"\b{re.escape(var)}\.id\b(?!\()",
                f"id({var})",
                result,
            )
            # 2) n.field → n.Tag.field（但 n.Tag.field 已前缀的不动；n.id 已被替换）
            result = re.sub(
                rf"\b{re.escape(var)}\.(?!{re.escape(tag)}\b)(\w+)\b",
                rf"{var}.{tag}.\1",
                result,
            )
        return result

    def _fix_equality(self, cypher: str) -> str:
        """WHERE 子句中单 ``=`` → ``==``。

        - 不动 ``>=`` / ``<=`` / ``!=`` / ``+=`` / 已有的 ``==``。
        - 不动 SET 子句（WHERE 在 SET 之前结束）。

        Args:
            cypher: Cypher 语句。

        Returns:
            转换后的语句。
        """
        terminator_alt = "|".join(sorted(self._WHERE_TERMINATORS))

        def _replace_where_body(match: re.Match[str]) -> str:
            where_body = match.group(1)
            # 单 = → ==，但前导字符不能是 < > = ! +，且不能已是 ==
            new_body = re.sub(r"(?<![<>=!+])=(?!=)", "==", where_body)
            return f"WHERE{new_body}"

        pattern = re.compile(
            rf"\bWHERE\b(.*?)(?=\b(?:{terminator_alt})\b|;|$)",
            re.IGNORECASE | re.DOTALL,
        )
        return pattern.sub(_replace_where_body, cypher)

    def _fix_start_end_node(self, cypher: str) -> str:
        """``startNode(r).id`` / ``endNode(r).id`` → ``id(<src_var>)`` / ``id(<tgt_var>)``。

        若 MATCH pattern 绑定了节点变量 ``(a)-[r]->(b)``，使用 ``id(a)`` / ``id(b)``；
        若变量匿名 ``()-[r]->()``，使用 NebulaGraph 的 ``src(r)`` / ``dst(r)`` 函数。

        Args:
            cypher: Cypher 语句。

        Returns:
            转换后的语句。
        """
        # 匹配 (a)-[r]->(b) / (a)-[r:REL]->(b) / (a)-[r*1..3]->(b) 等
        edge_pattern = re.compile(r"\(([A-Za-z_]\w*)?\)-\[[A-Za-z_]\w*(?::[^}\]]*)?\]->\(([A-Za-z_]\w*)?\)")
        edge_map: dict[str, tuple[str | None, str | None]] = {}
        for match in edge_pattern.finditer(cypher):
            src, tgt = match.group(1), match.group(2)
            # 提取边变量名（如 r）
            edge_var_match = re.search(r"\[([A-Za-z_]\w*)", match.group(0))
            if edge_var_match:
                edge_map[edge_var_match.group(1)] = (src, tgt)

        for edge_var, (src, tgt) in edge_map.items():
            escaped = re.escape(edge_var)
            if src:
                cypher = re.sub(
                    rf"\bstartNode\({escaped}\)\.id\b",
                    f"id({src})",
                    cypher,
                )
            else:
                cypher = re.sub(
                    rf"\bstartNode\({escaped}\)\.id\b",
                    f"src({edge_var})",
                    cypher,
                )
            if tgt:
                cypher = re.sub(
                    rf"\bendNode\({escaped}\)\.id\b",
                    f"id({tgt})",
                    cypher,
                )
            else:
                cypher = re.sub(
                    rf"\bendNode\({escaped}\)\.id\b",
                    f"dst({edge_var})",
                    cypher,
                )
        return cypher

    def _fix_reserved_words(self, cypher: str) -> str:
        """属性名是 nGQL 保留字时加反引号。

        仅匹配 ``var.Tag.field`` 形式（即已被 :meth:`_fix_property_access` 处理过），
        ``field`` 在 :attr:`NGQL_RESERVED` 中则改为 ``var.Tag.`field```。

        Args:
            cypher: Cypher/nGQL 语句。

        Returns:
            转换后的语句。
        """
        reserved_alt = "|".join(sorted(self.NGQL_RESERVED, key=len, reverse=True))

        def _replacer(match: re.Match[str]) -> str:
            var, tag, field = match.group(1), match.group(2), match.group(3)
            if field.lower() in self.NGQL_RESERVED:
                return f"{var}.{tag}.`{field}`"
            return match.group(0)

        # var.Tag.field（且 field 不紧跟 `(`，避免误伤 var.Tag.func(args)）
        pattern = re.compile(
            rf"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\.({reserved_alt})\b(?!\()",
            re.IGNORECASE,
        )
        return pattern.sub(_replacer, cypher)


# ---------- helpers ----------


def _split_top_level(text: str, sep: str) -> list[str]:
    """按 ``sep`` 切分字符串，但忽略 ``{}`` / ``[]`` / ``()`` 内的 sep。

    用于解析 inline property map（嵌套结构罕见，但稳健起见处理之）。

    Args:
        text: 输入字符串。
        sep: 分隔符（单字符）。

    Returns:
        切分后的字符串列表。
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "{[(":
            depth += 1
            current.append(char)
        elif char in "}])":
            depth = max(0, depth - 1)
            current.append(char)
        elif char == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts


__all__: Iterable[str] = ("CypherToNgqlAdapter",)

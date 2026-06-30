from __future__ import annotations

import re

from ontoagent.domain.schema import ONTOLOGY_RELATION_TYPES
from ontoagent.domain.shapes import PathExpression, PathToken

__all__ = ["PathCompiler"]

_VALID_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PathCompiler:
    """将 PathExpression 编译为参数化 Cypher MATCH 子句。

    生成的 MATCH 子句中:
    - 源节点变量默认为 `n`（可通过 `source_var` 参数覆盖）。
    - 中间跳节点变量为 `b0`, `b1`, ...。
    - 终点节点变量统一命名为 `collected`，便于调用方追加
      `RETURN collected.{collect_property} AS val`。

    防注入策略:
    - 关系类型必须出现在 `ONTOLOGY_RELATION_TYPES` 白名单中，否则抛 `ValueError`。
    - 目标标签做标识符格式校验（PascalCase 字符集）。
    - 量词经过严格解析，仅产生数字字面量。
    """

    def compile(self, expression: PathExpression, source_var: str = "n") -> tuple[str, dict]:
        """将 PathExpression 编译为参数化 Cypher MATCH 子句。

        Args:
            expression: 已解析的 PathExpression。
            source_var: 起始节点在 Cypher 中的变量名。

        Returns:
            由 (MATCH 子句, 参数字典) 组成的元组。SELF 路径返回 ('', {})。

        Raises:
            ValueError: 关系类型不在本体白名单或目标标签格式非法时。
        """
        if expression.is_self():
            return ("", {})

        self._validate_tokens(expression.tokens)
        self._validate_target_label(expression.target_label)

        fragments: list[str] = []
        hop_count = len(expression.tokens)
        for i, token in enumerate(expression.tokens):
            is_last = i == hop_count - 1
            if is_last:
                next_var = "collected"
                label_part = f":{expression.target_label}"
            else:
                next_var = f"b{i}"
                label_part = ""

            rel_pattern = self._build_rel_pattern(token, expression.max_depth)
            if i == 0:
                fragments.append(f"({source_var}){rel_pattern}({next_var}{label_part})")
            else:
                fragments.append(f"{rel_pattern}({next_var}{label_part})")

        cypher = "MATCH " + "".join(fragments)
        return (cypher, {})

    @staticmethod
    def _validate_tokens(tokens: list[PathToken]) -> None:
        """校验每个关系 token 都在本体白名单内。"""
        for token in tokens:
            if token.value not in ONTOLOGY_RELATION_TYPES:
                raise ValueError(f"未知关系类型 {token.value!r}，不在 ONTOLOGY_RELATION_TYPES 白名单中")

    @staticmethod
    def _validate_target_label(label: str) -> None:
        """校验目标标签是合法的 Neo4j 标识符，防止 Cypher 注入。"""
        if not label or not _VALID_LABEL_RE.match(label):
            raise ValueError(f"非法 target_label: {label!r}")

    @staticmethod
    def _build_rel_pattern(token: PathToken, max_depth: int) -> str:
        """根据 token 的量词与方向构造 Cypher 关系片段。

        Args:
            token: 单个路径 token。
            max_depth: 全局跳数上限，用于约束变长量词的上界。

        Returns:
            形如 `-[:REL*1..3]->` 或 `<-[:REL]-` 的字符串。
        """
        body = f":{token.value}{PathCompiler._quantifier_suffix(token.quantifier, max_depth)}"
        if token.reverse:
            return f"<-[{body}]-"
        return f"-[{body}]->"

    @staticmethod
    def _quantifier_suffix(quantifier: str, max_depth: int) -> str:
        """把 SHACL 量词翻译成 Cypher 的 `*m..n` 后缀。"""
        if quantifier == "":
            return ""
        if quantifier == "+":
            return f"*1..{max_depth}"
        if quantifier == "*":
            return f"*0..{max_depth}"
        if quantifier.startswith("{") and quantifier.endswith("}"):
            inner = quantifier[1:-1]
            if "," in inner:
                m_str, n_str = inner.split(",", 1)
                m = int(m_str)
                upper = min(int(n_str), max_depth) if n_str else max_depth
                return f"*{m}..{upper}"
            m = int(inner)
            return f"*{m}..{m}"
        raise ValueError(f"无法识别的量词: {quantifier!r}")

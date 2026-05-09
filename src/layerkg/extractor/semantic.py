from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from layerkg.exceptions import ExtractionError
from layerkg.schema import CodeEntity, ConceptEntity, DocEntity

VALID_SOURCE_TYPES = frozenset(
    {
        "function",
        "class",
        "interface",
        "module",
        "file",
        "readme",
        "module_doc",
        "api_doc",
        "comment",
        "wiki",
        "architecture_doc",
        "business_concept",
        "design_pattern",
        "api_contract",
        "data_model",
        "process",
    }
)


@dataclass
class SemanticRelation:
    """LLM 提取的语义关系（中间表示，待存入图谱）。"""

    source_name: str
    source_type: str
    target_name: str
    target_type: str
    relation_type: str
    confidence: float = 0.5
    reasoning: str = ""
    source_file_path: str = ""

    VALID_SEMANTIC_RELATION_TYPES = frozenset(
        {
            "semantic_impact",
            "describes",
            "illustrates",
            "derived_from",
        }
    )

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.source_name:
            raise ValueError("source_name cannot be empty")
        if not self.target_name:
            raise ValueError("target_name cannot be empty")
        if self.source_type not in VALID_SOURCE_TYPES:
            raise ValueError(f"Invalid source_type: {self.source_type}")
        if self.target_type not in VALID_SOURCE_TYPES:
            raise ValueError(f"Invalid target_type: {self.target_type}")
        if self.relation_type not in self.VALID_SEMANTIC_RELATION_TYPES:
            raise ValueError(f"Invalid relation_type: {self.relation_type}")
        if not (0 <= self.confidence <= 1):
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")


@dataclass
class ExtractionResult:
    """语义提取结果。"""

    relations: list[SemanticRelation] = field(default_factory=list)
    entities_processed: int = 0
    llm_calls: int = 0
    total_tokens: int = 0
    elapsed_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "relations_found": len(self.relations),
            "entities_processed": self.entities_processed,
            "llm_calls": self.llm_calls,
            "total_tokens": self.total_tokens,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "errors": self.errors,
        }


class SemanticExtractor:
    """语义关系提取器。"""

    VALID_SEMANTIC_RELATIONS = frozenset(
        {
            "semantic_impact",
            "describes",
            "illustrates",
            "derived_from",
        }
    )

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        model: str = "qwen3.5:9b",
        *,
        batch_size: int = 20,
        max_retries: int = 3,
        timeout: float = 60.0,
        temperature: float = 0.1,
    ) -> None:
        """初始化。"""
        self._ollama_url = ollama_url
        self._model = model
        self._batch_size = batch_size
        self._max_retries = max_retries
        self._timeout = timeout
        self._temperature = temperature
        self._logger = logging.getLogger(__name__)
        self._client = httpx.Client()

    def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if hasattr(self, "_client"):
            self._client.close()

    def extract(
        self,
        entities: list[CodeEntity],
        *,
        doc_entities: list[DocEntity] | None = None,
        concept_entities: list[ConceptEntity] | None = None,
    ) -> ExtractionResult:
        """从实体列表中提取语义关系。

        Args:
            entities: 代码实体列表。
            doc_entities: 文档实体列表（可选，用于 describes 关系）。
            concept_entities: 概念实体列表（可选，用于 derived_from 关系）。

        Returns:
            ExtractionResult 提取结果。
        """
        start = time.time()
        all_relations: list[SemanticRelation] = []
        errors: list[str] = []
        total_tokens = 0
        llm_calls = 0

        # 1. 将实体按批次分组
        batches = self._create_batches(entities)

        # 2. 逐批调用 LLM
        total_batches = len(batches)
        self._logger.info("[Semantic] Starting: %d entities in %d batches", len(entities), total_batches)
        for i, batch in enumerate(batches, 1):
            t_batch = time.time()
            try:
                batch_relations, batch_tokens = self.extract_batch(batch)
                all_relations.extend(batch_relations)
                total_tokens += batch_tokens
                elapsed_batch = time.time() - t_batch
                self._logger.info(
                    "[Semantic] Batch %d/%d: %d relations, %d tokens (%.1fs)",
                    i, total_batches, len(batch_relations), batch_tokens, elapsed_batch,
                )
            except ExtractionError as e:
                errors.append(str(e))
                self._logger.warning("[Semantic] Batch %d/%d FAILED: %s", i, total_batches, e)
            finally:
                llm_calls += 1

        # 3. 可选：跨类型关系（Code-Doc, Code-Concept）
        if doc_entities:
            doc_relations = self._extract_cross_type_relations(entities, doc_entities, "describes")
            all_relations.extend(doc_relations)
        if concept_entities:
            concept_relations = self._extract_cross_type_relations(entities, concept_entities, "derived_from")
            all_relations.extend(concept_relations)

        self._logger.info(
            "[Semantic] Complete: %d relations from %d batches, %d tokens total",
            len(all_relations), total_batches, total_tokens,
        )
        return ExtractionResult(
            relations=all_relations,
            entities_processed=len(entities),
            llm_calls=llm_calls,
            total_tokens=total_tokens,
            elapsed_ms=(time.time() - start) * 1000,
            errors=errors,
        )

    def _call_llm(self, prompt: str) -> tuple[str, int]:
        """调用 Ollama chat API。

        Args:
            prompt: 用户 prompt。

        Returns:
            (响应文本, token消耗) 元组。

        Raises:
            ExtractionError: 当 API 调用失败时。
        """
        try:
            response = self._client.post(
                f"{self._ollama_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": self._temperature,
                        "num_predict": 1024,
                    },
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
            content = data["message"]["content"]
            tokens = (data.get("prompt_eval_count", 0) or 0) + (data.get("eval_count", 0) or 0)
            return content, tokens
        except httpx.HTTPError as e:
            raise ExtractionError(f"Ollama API call failed: {e}") from e

    def _create_batches(self, entities: list[CodeEntity]) -> list[list[CodeEntity]]:
        """将实体列表按 batch_size 分批。

        Args:
            entities: 实体列表。

        Returns:
            分批后的二维列表。
        """
        if not entities:
            return []
        return [entities[i : i + self._batch_size] for i in range(0, len(entities), self._batch_size)]

    def extract_batch(self, entities: list[CodeEntity]) -> tuple[list[SemanticRelation], int]:
        """处理单个批次，调用 LLM 提取语义关系。

        Args:
            entities: 一批代码实体（≤ batch_size）。

        Returns:
            (提取到的语义关系列表, token消耗) 元组。

        Raises:
            ExtractionError: 当重试耗尽后仍然失败时。
        """
        if not entities:
            return [], 0

        prompt = self._build_prompt(entities)

        for attempt in range(self._max_retries):
            try:
                response_text, tokens = self._call_llm(prompt)
                relations = self._parse_response(response_text)
                file_paths = {e.name: (e.file_path or "") for e in entities}
                for rel in relations:
                    if not rel.source_file_path:
                        rel.source_file_path = file_paths.get(rel.source_name, "")
                return relations, tokens
            except ExtractionError:
                if attempt == self._max_retries - 1:
                    raise
                self._logger.warning("[Semantic] Batch retry %d/%d", attempt + 2, self._max_retries)
                import time

                time.sleep(2**attempt)

        return [], 0

    def __enter__(self) -> SemanticExtractor:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _build_prompt(entities: list[CodeEntity]) -> str:
        """构建 LLM prompt。"""
        entity_descriptions = []
        for e in entities:
            desc = f"- {e.entity_type} `{e.name}`"
            if e.source:
                source_preview = e.source[:200] + "..." if len(e.source or "") > 200 else (e.source or "")
                desc += f": {source_preview}"
            if e.file_path:
                desc += f" (file: {e.file_path})"
            entity_descriptions.append(desc)

        entities_text = "\n".join(entity_descriptions)

        return f"""You are a code architecture analyst. Analyze these code entities and extract TWO kinds of output:

1. **semantic_impact** relations: code-to-code influence (A's changes affect B)
2. **Concepts**: abstract design patterns or business concepts embodied in the code

Entities:
{entities_text}

## Step 1: Identify Code-to-Code Impact (semantic_impact)
Find pairs where Entity A's behavior change would likely affect Entity B.
- source_type / target_type: use code types (function, class, module)

## Step 2: Identify Concepts (MOST IMPORTANT)
For each entity, ask: "What design pattern, architecture concept, or business concept does this implement?"

Common concepts to recognize:
- Design patterns: Observer, Strategy, Factory, Singleton, Adapter, Facade, Proxy, Builder, Iterator, Pipeline, Cache, Index, Repository
- Architecture concepts: Message Queue, Event Bus, Middleware, ORM, Dependency Injection, Configuration Management
- Business concepts: Authentication, Authorization, Rate Limiting, Data Validation, Logging, Error Handling

When you identify a concept:
- source: the code entity name
- source_type: the entity's type (function, class, etc.)
- target: the concept name (e.g. "Cache Pattern", "Repository Pattern", "Dependency Injection")
- target_type: one of "design_pattern", "business_concept", "api_contract", "data_model", "process"
- relation_type: "derived_from"

## Output Format
Return ONLY a JSON object:
{{{{
  "relations": [
    {{{{
      "source": "EntityName",
      "source_type": "function",
      "target": "EntityName",
      "target_type": "class",
      "relation_type": "semantic_impact",
      "confidence": 0.8,
      "reasoning": "Brief explanation"
    }}}},
    {{{{
      "source": "ClassName",
      "source_type": "class",
      "target": "Cache Pattern",
      "target_type": "design_pattern",
      "relation_type": "derived_from",
      "confidence": 0.9,
      "reasoning": "This class manages cached data with get/set operations"
    }}}}
  ]
}}}}

## Rules
- Only include relations you are confident about (confidence >= 0.5)
- relation_type must be one of: semantic_impact, derived_from
- target_type for concepts MUST be one of: design_pattern, business_concept, api_contract, data_model, process
- You MUST identify at least some concepts — do NOT only output code-to-code relations
- Return ONLY the JSON, no additional text"""

    @staticmethod
    def _parse_response(response_text: str) -> list[SemanticRelation]:
        """解析 LLM 响应为 SemanticRelation 列表。"""
        text = response_text.strip()
        # 移除 qwen3.5 等模型的 <think...</think 标签
        text = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.DOTALL).strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ExtractionError(f"LLM response is not valid JSON: {e}") from e

        if not isinstance(data, dict) or "relations" not in data:
            raise ExtractionError("LLM response missing 'relations' key")

        relations = []
        for item in data["relations"]:
            try:
                rel = SemanticRelation(
                    source_name=item["source"],
                    source_type=item["source_type"],
                    target_name=item["target"],
                    target_type=item["target_type"],
                    relation_type=item["relation_type"],
                    confidence=float(item.get("confidence", 0.5)),
                    reasoning=item.get("reasoning", ""),
                    source_file_path="",
                )
                if SemanticExtractor._validate_relation(rel):
                    relations.append(rel)
            except (KeyError, ValueError):
                continue

        return relations

    @staticmethod
    def _validate_relation(rel: SemanticRelation) -> bool:
        """校验单个语义关系的有效性。"""
        if not rel.source_name:
            return False
        if rel.relation_type not in SemanticExtractor.VALID_SEMANTIC_RELATIONS:
            return False
        return not rel.confidence < 0.5

    def _extract_cross_type_relations(
        self,
        code_entities: list[CodeEntity],
        other_entities: list[DocEntity | ConceptEntity],
        relation_type: str,
    ) -> list[SemanticRelation]:
        """提取跨类型关系（Code-Doc 或 Code-Concept）。

        Args:
            code_entities: 代码实体列表。
            other_entities: 文档实体或概念实体列表。
            relation_type: 关系类型（describes 或 derived_from）。

        Returns:
            提取到的语义关系列表。
        """
        relations: list[SemanticRelation] = []

        for other in other_entities:
            # 查找名称出现在 source 中的 code 实体
            for code in code_entities:
                if code.source and other.name in code.source:
                    # 对于 describes 关系：source 是 doc，target 是 code
                    # 对于 derived_from 关系：source 是 code，target 是 concept
                    if relation_type == "describes":
                        rel = SemanticRelation(
                            source_name=other.name,
                            source_type=other.entity_type,
                            target_name=code.name,
                            target_type=code.entity_type,
                            relation_type=relation_type,
                            confidence=0.7,
                            reasoning=f"{other.name} appears in {code.name} source code",
                            source_file_path=code.file_path or "",
                        )
                    else:  # derived_from
                        rel = SemanticRelation(
                            source_name=code.name,
                            source_type=code.entity_type,
                            target_name=other.name,
                            target_type=other.entity_type,
                            relation_type=relation_type,
                            confidence=0.7,
                            reasoning=f"{code.name} implements {other.name}",
                            source_file_path=code.file_path or "",
                        )
                    relations.append(rel)

        return relations

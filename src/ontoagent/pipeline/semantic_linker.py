"""语义链接逻辑：文档→代码关联、概念对齐、语义关系处理。

抽离自 ``OntoAgentBuilder``，所有函数均为无状态函数，依赖通过参数显式传入。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from ontoagent.config import OntoAgentConfig
from ontoagent.domain.schema import CodeEntity, ConceptEntity, DocEntity, Relation
from ontoagent.parsing.extractor.semantic import SemanticExtractor, SemanticRelation
from ontoagent.pipeline import builder_utils
from ontoagent.pipeline.aligner import ConceptAligner
from ontoagent.store.chroma_store import ChromaStore

if TYPE_CHECKING:
    pass

# 概念类型的 entity_type 集合
CONCEPT_ENTITY_TYPES = frozenset(
    {
        "business_concept",
        "design_pattern",
        "api_contract",
        "data_model",
        "process",
        "message_topic",
    }
)

# 代码类型的 entity_type 集合（路径 B 可处理的）
CODE_ENTITY_TYPES = frozenset(
    {
        "function",
        "class",
        "interface",
        "module",
        "file",
        "enum",
        "record",
        "field",
    }
)


def link_docs_to_code(
    doc_entities: list[DocEntity],
    entity_index: dict[tuple[str, str, str], list[str]],
) -> list[Relation]:
    """链接文档到代码实体，生成 ``describes`` 关系。

    匹配策略（按优先级）：
        1. 完整路径匹配（带边界检查，防子串误匹配）
        2. 文件 basename 匹配（带边界检查）
        3. 函数名匹配（从 Markdown 代码块提取标识符，长度 > 3）

    每个文档最多生成 50 条关系。

    Args:
        doc_entities: 文档实体列表。
        entity_index: 实体索引，键为 ``(entity_type, file_path, name)`` 三元组。

    Returns:
        ``describes`` 关系列表。
    """
    describes_rels: list[Relation] = []
    max_rels_per_doc = 50

    for doc in doc_entities:
        count = 0
        content = doc.content or ""

        for (_entity_type, file_path, name), entity_ids in entity_index.items():
            if count >= max_rels_per_doc:
                break

            # 路径匹配（需要 file_path）
            if file_path:
                doc_content = doc.content or ""
                if file_path in doc_content and _is_boundary_match(doc_content, file_path):
                    describes_rels.append(
                        Relation(
                            source_id=doc.id,
                            target_id=entity_ids[0],
                            relation_type="describes",
                        )
                    )
                    count += 1
                    continue

                # 文件名匹配：检查 doc.content 是否包含文件 basename
                filename = os.path.basename(file_path)
                if filename and filename != file_path and filename in doc_content and _is_boundary_match(doc_content, filename):
                        describes_rels.append(
                            Relation(
                                source_id=doc.id,
                                target_id=entity_ids[0],
                                relation_type="describes",
                            )
                        )
                        count += 1
                        continue

            # 函数名匹配：从代码块提取标识符（不依赖 file_path）
            if name in builder_utils.extract_identifiers_from_code(content) and len(name) > 3:
                describes_rels.append(
                    Relation(
                        source_id=doc.id,
                        target_id=entity_ids[0],
                        relation_type="describes",
                    )
                )
                count += 1

    return describes_rels


def _is_boundary_match(text: str, needle: str) -> bool:
    """检查 ``needle`` 在 ``text`` 中所有出现位置的左右字符是否均为边界字符。

    Args:
        text: 被搜索的文本。
        needle: 搜索串。

    Returns:
        True 表示首次出现位置满足左右边界。
    """
    doc_idx = text.find(needle)
    if doc_idx < 0:
        return False
    left_ok = doc_idx == 0 or text[doc_idx - 1] in builder_utils.BOUNDARY_CHARS
    right_ok = (
        doc_idx + len(needle) >= len(text)
        or text[doc_idx + len(needle)] in builder_utils.BOUNDARY_CHARS
    )
    return left_ok and right_ok


def check_llm_available(config: OntoAgentConfig) -> bool:
    """检查语义提取 LLM 服务是否可用。

    OpenAI provider：仅校验 API key；Ollama provider：探测 ``/api/tags``。

    Args:
        config: OntoAgent 配置。

    Returns:
        True 表示服务可用。
    """
    provider = config.semantic_llm_provider
    if provider == "openai":
        return bool(config.semantic_llm_api_key)
    try:
        resp = httpx.get(f"{config.ollama_base_url}/api/tags", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False


def make_semantic_extractor(config: OntoAgentConfig) -> SemanticExtractor:
    """根据配置构造新的 SemanticExtractor 实例。

    Args:
        config: OntoAgent 配置。

    Returns:
        SemanticExtractor 实例。
    """
    return SemanticExtractor(
        ollama_url=config.ollama_base_url,
        model=config.llm_model,
        batch_size=config.semantic_batch_size,
        num_predict=config.semantic_num_predict,
        provider=config.semantic_llm_provider,
        api_key=config.semantic_llm_api_key,
        base_url=config.semantic_llm_base_url,
    )


def make_concept_aligner(chroma_store: ChromaStore) -> ConceptAligner:
    """构造 ConceptAligner（从空概念列表开始）。

    Args:
        chroma_store: ChromaDB 存储实例。

    Returns:
        ConceptAligner 实例。
    """
    return ConceptAligner(
        chroma_store=chroma_store,
        concepts=[],
    )


def build_entity_index(
    entities: list[CodeEntity | ConceptEntity | DocEntity],
    repo_root: Path,
) -> dict[tuple[str, str, str], list[str]]:
    """构建实体索引，用于名称解析。

    Args:
        entities: 实体列表。
        repo_root: 仓库根目录路径。

    Returns:
        索引字典，键为 ``(entity_type, file_path, name)`` 三元组，值为 ID 列表。
    """
    index: dict[tuple[str, str, str], list[str]] = {}
    for e in entities:
        file_path = getattr(e, "file_path", None)  # ConceptEntity 没有 file_path
        entity_type = e.entity_type
        key = (entity_type, builder_utils.normalize_path(file_path, repo_root), e.name)
        if key not in index:
            index[key] = []
        index[key].append(e.id)
    return index


def fuzzy_lookup_entity(
    entity_index: dict[tuple[str, str, str], list[str]],
    entity_type: str,
    name: str,
) -> list[str]:
    """模糊查找实体 ID：按 ``(type, name)`` 匹配，忽略 file_path。

    Args:
        entity_index: 实体索引。
        entity_type: 实体类型。
        name: 实体名称。

    Returns:
        匹配到的 ID 列表。
    """
    candidates: list[str] = []
    for key, ids in entity_index.items():
        if key[0] == entity_type and key[2] == name:
            candidates.extend(ids)
    return candidates


def process_semantic_relations(
    relations: list[SemanticRelation],
    entity_index: dict[tuple[str, str, str], list[str]],
    repo_root: Path,
    aligner: ConceptAligner,
) -> tuple[list[ConceptEntity], list[Relation], int, dict[tuple[str, str], tuple[str, str]]]:
    """处理语义关系：概念对齐 + ID 解析。

    路径 A：``target_type ∈ CONCEPT_ENTITY_TYPES`` → 批量对齐概念（exact/alias/vector/graph/none）。
    路径 B：``target_type ∈ CODE_ENTITY_TYPES`` → 用 entity_index 精确/模糊解析。

    Args:
        relations: SemanticExtractor 提取的语义关系。
        entity_index: Stage 1-2 构建的实体索引。
        repo_root: 仓库根目录。
        aligner: ConceptAligner 实例（用于路径 A 的对齐与新概念注册）。

    Returns:
        ``(新概念列表, Relation 列表, 跳过数量, 类型映射)`` 元组。
        类型映射：``{(source_id, target_id): (source_type, target_type)}``。
    """
    new_concepts: list[ConceptEntity] = []
    resolved: list[Relation] = []
    skipped = 0
    type_mapping: dict[tuple[str, str], tuple[str, str]] = {}

    # 路径 A：收集所有概念目标的 unique target_name
    concept_targets: dict[str, tuple[str, str]] = {}  # target_name → (target_type, reasoning)
    concept_relations: list[SemanticRelation] = []
    code_relations: list[SemanticRelation] = []

    for rel in relations:
        if rel.target_type in CONCEPT_ENTITY_TYPES:
            concept_targets.setdefault(rel.target_name, (rel.target_type, rel.reasoning or ""))
            concept_relations.append(rel)
        elif rel.target_type in CODE_ENTITY_TYPES:
            code_relations.append(rel)
        else:
            # ResourceEntity, DocEntity 等 → 跳过
            skipped += 1

    # 路径 A：批量对齐概念
    if concept_targets:
        align_results = aligner.align_batch(list(concept_targets.keys()))
        concept_id_map: dict[str, str] = {}  # target_name → concept_id

        for target_name, align_result in zip(concept_targets.keys(), align_results, strict=True):
            if align_result.match_type == "none":
                target_type, description = concept_targets[target_name]
                concept = ConceptEntity(
                    name=target_name,
                    entity_type=target_type,
                    description=description,
                )
                new_concepts.append(concept)
                concept_id_map[target_name] = concept.id
                aligner.add_concept(concept)
            else:
                concept_id_map[target_name] = align_result.concept_id

        # 构建路径 A 的 Relation
        for rel in concept_relations:
            source_key = (
                rel.source_type,
                builder_utils.normalize_path(rel.source_file_path, repo_root),
                rel.source_name,
            )
            source_ids = entity_index.get(source_key, [])
            if not source_ids:
                skipped += 1
                continue
            target_id = concept_id_map.get(rel.target_name)
            if not target_id:
                skipped += 1
                continue
            source_id = source_ids[0]
            resolved.append(
                Relation(
                    source_id=source_id,
                    target_id=target_id,
                    relation_type=rel.relation_type,
                )
            )
            type_mapping[(source_id, target_id)] = (rel.source_type, concept_targets[rel.target_name][0])

    # 路径 B：代码目标，用 entity_index 解析
    for rel in code_relations:
        source_key = (
            rel.source_type,
            builder_utils.normalize_path(rel.source_file_path, repo_root),
            rel.source_name,
        )
        source_ids = entity_index.get(source_key, [])
        if not source_ids:
            source_ids = fuzzy_lookup_entity(entity_index, rel.source_type, rel.source_name)
        target_ids = fuzzy_lookup_entity(entity_index, rel.target_type, rel.target_name)
        if not source_ids or not target_ids:
            skipped += 1
            continue
        source_id = source_ids[0]
        target_id = target_ids[0]
        resolved.append(
            Relation(
                source_id=source_id,
                target_id=target_id,
                relation_type=rel.relation_type,
            )
        )
        type_mapping[(source_id, target_id)] = (rel.source_type, rel.target_type)

    return new_concepts, resolved, skipped, type_mapping


def resolve_semantic_names(
    relations: list[SemanticRelation],
    index: dict[tuple[str, str, str], list[str]],
    repo_root: Path,
    logger: logging.Logger,
) -> tuple[list[Relation], int]:
    """将语义关系中的名称解析为实体 ID。

    Args:
        relations: 语义关系列表（使用名称）。
        index: 实体索引。
        repo_root: 仓库根目录路径。
        logger: 日志器。

    Returns:
        ``(解析后的 Relation 列表, 跳过数量)`` 元组。

    Note:
        如果 source_file_path 为空或多个同名实体存在于不同文件中，
        source 可能匹配到错误的实体。这是已知的近似匹配限制，
        后续可用 source_context 或 qualified_name 增强。
    """
    resolved: list[Relation] = []
    skipped = 0
    for rel in relations:
        source_key = (
            rel.source_type,
            builder_utils.normalize_path(rel.source_file_path, repo_root),
            rel.source_name,
        )
        target_key = (rel.target_type, "", rel.target_name)
        source_ids = index.get(source_key, [])
        target_ids = index.get(target_key, [])
        if not source_ids or not target_ids:
            logger.warning(
                "Cannot resolve semantic relation: %s -> %s",
                rel.source_name,
                rel.target_name,
            )
            skipped += 1
            continue
        resolved.append(
            Relation(
                source_id=source_ids[0],
                target_id=target_ids[0],
                relation_type=rel.relation_type,
            )
        )
    return resolved, skipped


# 重导出，供外部按子模块路径导入（如 ``from ontoagent.pipeline.semantic_linker import ExtractedRelation``）
__all__ = [
    "CODE_ENTITY_TYPES",
    "CONCEPT_ENTITY_TYPES",
    "build_entity_index",
    "check_llm_available",
    "fuzzy_lookup_entity",
    "link_docs_to_code",
    "make_concept_aligner",
    "make_semantic_extractor",
    "process_semantic_relations",
    "resolve_semantic_names",
]

from __future__ import annotations

# LayerKG - 基于本体驱动的可更新知识图谱引擎
from layerkg.aligner import NO_MATCH, AlignResult, ConceptAligner
from layerkg.builder import BuildResult, LayerKGBuilder
from layerkg.change_detector import ChangedFile, ChangeType, GitChangeDetector, GitStatus, SHA256Cache
from layerkg.chroma_store import ChromaStore, OllamaEmbeddingFunction
from layerkg.config import LayerKGConfig
from layerkg.exceptions import EmbeddingError, LayerKGError, SchemaValidationError, StoreError
from layerkg.graph_store import GraphStore
from layerkg.impact_propagator import (
    DEFAULT_DECAY_SCHEDULE,
    DEFAULT_WEIGHT_MATRIX,
    ImpactedNode,
    ImpactPropagator,
    ImpactReport,
    ImpactSeverity,
    PropagationDirection,
)
from layerkg.schema import (
    RELATION_TYPE_TO_NEO4J,
    VALID_RELATION_TYPES,
    ChangeSetEntity,
    CodeEntity,
    ConceptEntity,
    DocEntity,
    ModuleEntity,
    Relation,
    ResourceEntity,
)

__all__ = [
    # config
    "LayerKGConfig",
    # exceptions
    "LayerKGError",
    "SchemaValidationError",
    "StoreError",
    "EmbeddingError",
    # graph store
    "GraphStore",
    # chroma store
    "ChromaStore",
    "OllamaEmbeddingFunction",
    # aligner
    "ConceptAligner",
    "AlignResult",
    "NO_MATCH",
    # builder
    "LayerKGBuilder",
    "BuildResult",
    # change detector
    "ChangeType",
    "ChangedFile",
    "GitStatus",
    "SHA256Cache",
    "GitChangeDetector",
    # impact propagator
    "ImpactPropagator",
    "ImpactReport",
    "ImpactedNode",
    "ImpactSeverity",
    "PropagationDirection",
    "DEFAULT_WEIGHT_MATRIX",
    "DEFAULT_DECAY_SCHEDULE",
    # schema - entities
    "CodeEntity",
    "ConceptEntity",
    "DocEntity",
    "ResourceEntity",
    "ModuleEntity",
    "ChangeSetEntity",
    # schema - relations
    "Relation",
    "VALID_RELATION_TYPES",
    "RELATION_TYPE_TO_NEO4J",
]

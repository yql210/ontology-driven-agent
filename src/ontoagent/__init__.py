from __future__ import annotations

from ontoagent.config import OntoAgentConfig
from ontoagent.domain.exceptions import EmbeddingError, OntoAgentError, SchemaValidationError, StoreError
from ontoagent.domain.schema import (
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

# OntoAgent - 基于本体驱动的可更新知识图谱引擎
from ontoagent.pipeline.aligner import NO_MATCH, AlignResult, ConceptAligner
from ontoagent.pipeline.builder import BuildResult, OntoAgentBuilder
from ontoagent.pipeline.change_detector import ChangedFile, ChangeType, GitChangeDetector, GitStatus, SHA256Cache
from ontoagent.pipeline.impact_propagator import (
    DEFAULT_DECAY_SCHEDULE,
    DEFAULT_WEIGHT_MATRIX,
    ImpactedNode,
    ImpactPropagator,
    ImpactReport,
    ImpactSeverity,
    PropagationDirection,
)
from ontoagent.store.chroma_store import ChromaStore, OllamaEmbeddingFunction
from ontoagent.store.graph_store import GraphStore

__all__ = [
    # config
    "OntoAgentConfig",
    # exceptions
    "OntoAgentError",
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
    "OntoAgentBuilder",
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

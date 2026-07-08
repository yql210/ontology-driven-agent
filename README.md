# OntoAgent — Ontology-Driven Knowledge Graph Engine

<p align="center">
  <strong>Query your codebase with natural language</strong>
</p>

<p align="center">
  <a href="https://github.com/anthropics/claude-code">
    <img src="https://img.shields.io/badge/Python-3.13+-blue.svg" alt="Python">
  </a>
  <img src="https://img.shields.io/badge/tests-1714%20passed-green.svg" alt="Tests">
  <img src="https://img.shields.io/badge/src-114%20files%2C%2021K%20LOC-orange.svg" alt="LOC">
  <img src="https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg" alt="License">
</p>

---

OntoAgent is an **ontology-driven intelligent DevOps platform** that automatically constructs knowledge graphs from source code and documentation, enabling architectural queries, dependency analysis, and change impact assessment through natural language.

```
"Who depends on merge_node()?"  →  BFS impact propagation
"What design patterns exist?"   →  Concept alignment + semantic search
"Which module is most complex?" →  Agent multi-step reasoning
```

## Core Features

- **Auto Construction** — Tree-sitter AST parsing + LLM semantic extraction, one-command graph building
- **Incremental Updates** — git diff → change detection → bidirectional BFS propagation → selective regeneration
- **Concept Alignment** — Four-step aligner (exact → alias → vector → graph structure) solving term drift
- **Agent Orchestration** — LangGraph ReAct Agent with 8 tools for complex reasoning
- **Butler Perpetual Agent** — EventBus + Handler pattern, knowledge introspection + consistency guard + skill evolution
- **MCP Server** — FastAPI server exposing tools via standard protocol
- **Data Provenance** — Credibility scoring and source tracking for all entities

## Architecture

OntoAgent V3.4 adopts a four-layer architecture: **Intent → Control → Capability → Semantic**.

```
┌─────────────────────────────────────────────────────┐
│  Intent — 意图层                                     │
│  Agent 识别意图，直接调度 Action                       │
├─────────────────────────────────────────────────────┤
│  Control — 控制层                                     │
│  ActionExecutor · SAGA 事务 · Submission Criteria     │
├─────────────────────────────────────────────────────┤
│  Capability — 能力层                                  │
│  Function (通用 + 领域) · FunctionRunner · Connector   │
├─────────────────────────────────────────────────────┤
│  Semantic — 语义层                                    │
│  Schema · GraphStore (Neo4j + ChromaDB)               │
└─────────────────────────────────────────────────────┘
```

| Layer | Responsibility | Key Components |
|-------|---------------|----------------|
| **Intent** | Parse natural-language requests into structured intents; dispatch to Actions | LangGraph ReAct Agent |
| **Control** | Orchestrate execution with transaction safety, fault tolerance, and acceptance criteria | ActionExecutor, TransactionManager (SAGA), CircuitBreaker, Submission Criteria |
| **Capability** | Composable domain functions as reusable building blocks | Function (General + Domain), FunctionRunner, Connector framework, MCP server |
| **Semantic** | Persistent knowledge infrastructure with ontology-driven validation | Schema (6 entities, 11 relations), Neo4j, ChromaDB, Schema constraints |

## Quick Start

```bash
# Clone and install dependencies
git clone https://gitee.com/sinxyql/ontology-driven-agent.git
cd ontology-driven-agent
uv sync

# Configure environment
cp .env.example .env
# Edit .env with your Neo4j credentials and LLM API keys

# Build knowledge graph
uv run ontoagent build ./your-repo --clear

# Query with natural language
uv run ontoagent query "functions handling user authentication"
```

## Docker Deployment

```bash
# Start Neo4j + ChromaDB + OntoAgent
docker compose up -d

# Run CLI commands
docker compose run --rm ontoagent ontoagent build ./repo
```

## OntoAgent Schema

### 6 Entities

| Entity | Types | Description |
|--------|-------|-------------|
| `CodeEntity` | function, class, interface, module, file, enum, record, field | Code structure |
| `ConceptEntity` | business_concept, design_pattern, api_contract, data_model, process | Business & design concepts |
| `DocEntity` | readme, module_doc, api_doc, comment, wiki, architecture_doc | Documentation |
| `ResourceEntity` | image, diagram, pdf, config, schema_file, log | Resources |
| `ModuleEntity` | functional module | Clustering results |
| `ChangeSetEntity` | change set | Git commit tracking |

### 11 Relationships

| Category | Relationships | Description |
|----------|--------------|-------------|
| Structural (AST) | `calls`, `extends`, `implements`, `imports`, `contains` | Code static structure |
| Semantic (LLM) | `semantic_impact`, `describes`, `illustrates`, `derived_from` | Semantic associations |
| Change | `changed_in`, `affects` | Change impact tracking |

## CLI Commands

```bash
ontoagent build <repo>      # Full build (7-stage pipeline)
ontoagent query <text>      # Semantic search
ontoagent update --since <rev>  # Incremental update
ontoagent migrate           # Schema migration
ontoagent serve             # Start MCP server
```

## Project Structure

```
src/ontoagent/
├── schema.py              # 6 entities + 11 relations
├── graph_store.py         # GraphStore abstract interface
├── neo4j_store.py         # Neo4j with constraints + version registry
├── chroma_store.py        # ChromaDB vector storage
├── provenance.py          # Data provenance & credibility
├── schema_version.py      # Schema version tracking
├── config.py              # Configuration management
├── exceptions.py          # Exception hierarchy
├── builder.py             # Full builder (7-stage pipeline)
├── incremental.py         # Incremental updater (bidirectional BFS)
├── clustering.py          # Concept clustering
├── schema_constraints.py  # Ontology semantic constraints
├── cli.py                 # Click CLI (build/query/update/migrate/serve)
├── parser/                # Tree-sitter multi-language parsing (Python+Java)
├── extractor/             # Relation extraction (structural + semantic)
├── agent/                 # LangGraph Agent (ReAct + 8 tools)
├── butler/                # Event-driven engine (EventBus + Handler + Watcher)
├── migrations/            # Schema migration framework
└── web/                   # FastAPI MCP server
```

## Tech Stack

| Category | Technology | Version |
|----------|-----------|---------|
| Language | Python | 3.13+ |
| Package Manager | uv | 0.11+ |
| AST Parsing | Tree-sitter | latest |
| Graph Database | Neo4j | 5.x |
| Vector Database | ChromaDB | latest |
| Code Embedding | Qwen2.5-0.5B-Coder (Ollama) | latest |
| Agent Framework | LangGraph | latest |
| CLI | Click | latest |
| Formatting | ruff | latest |
| Type Checking | pyright | latest |

## Project Stats

| Metric | Value |
|--------|-------|
| Source Code | 54 files, 12,157 LOC |
| Test Code | 72 files, 23,145 LOC |
| Tests | 1,274 passed, 1 skipped |
| Latest Commit | 54f5763 |

## License

MIT

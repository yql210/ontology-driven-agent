# OntoAgent — Ontology-Driven AI Agent Constraint Framework

<p align="center">
  <strong>Graph traversal IS the permission check.</strong><br>
  Constraining what AI Agents can do at runtime — not just what they can read.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/tests-1885-green.svg" alt="Tests">
  <img src="https://img.shields.io/badge/src-113%20files%2C%2023K%20LOC-orange.svg" alt="LOC">
  <img src="https://img.shields.io/badge/version-0.2.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg" alt="License">
</p>

---

## Why OntoAgent?

When LLM Agents evolve from "read-only assistants" to "autonomous actors" — editing code, writing databases, triggering deployments — a new safety question emerges: **who constrains the Agent's actions?**

Traditional RBAC/ABAC answers *"who can do what"* — it cannot answer semantic-level questions:

> "This code processes credit card numbers. Does the Agent have permission to refactor it?"

The answer doesn't depend on *who the operator is* (RBAC), nor on *what conditions are met* (OPA) — it depends on **what the operation target is semantically connected to**. OntoAgent makes those connections first-class citizens of the permission system.

**Core idea:** Business ontology's relation chains automatically become permission boundaries. Graph traversal along those relations **is** the permission check.

```
Agent wants to UPDATE CodeEntity "process_payment()"
    │
    ▼
┌─────────────────────────────────────────────┐
│  Shape Constraint Evaluation                │
│  PROCESSES_DATA → DataAsset{sensitivity:    │
│    restricted}  ⟹  BLOCK                    │
└─────────────────────────────────────────────┘
    │
    ▼
  DENY — or escalate to human approval
```

---

## Architecture

V3.4 four-layer architecture — each layer depends only on the one below, never across or upward.

```
┌─────────────────────────────────────────────────────────┐
│  Intent — Agent identifies intent → dispatches Action   │
│  (LangGraph ReAct Agent + 8 tools)                      │
├─────────────────────────────────────────────────────────┤
│  Control — Shape constraints · Approval gate · Audit    │
│  (ShapeEvaluator + DecisionFuser + ApprovalGate)        │
├─────────────────────────────────────────────────────────┤
│  Capability — Function registry · FunctionRunner        │
│  (General + Domain functions, retry/circuit-breaker)    │
├─────────────────────────────────────────────────────────┤
│  Semantic — Schema · GraphStore · Shape rules           │
│  (12 entities · 29 relations · Neo4j/NebulaGraph)       │
└─────────────────────────────────────────────────────────┘
```

### Intent → Execution flow

1. Agent receives natural language → matches `intent_type` via trigger hints.
2. Agent calls `express_intent(intent_type, target, params)`.
3. `ActionExecutor` resolves entity → checks submission criteria → invokes Function via `FunctionRunner`.
4. **Before execution:** Shape constraints evaluate the operation context by traversing ontology relations in the graph.
5. **Decision:** `DENY` → blocked; `WARN`/`BLOCK` → escalate to `ApprovalGate`; `ALLOW` → proceed.
6. **After execution:** Full audit trail recorded.

---

## Three-Layer Constraint System

The heart of OntoAgent — each layer adds a dimension of control:

| Layer | What it does | Config source |
|-------|-------------|---------------|
| **Layer 1: Ontology-derived** | Auto-derive constraints from entity relations (e.g., `CodeEntity --PROCESSES_DATA--> DataAsset`) | `business_ontology.yaml` + schema registry |
| **Layer 2: Overrides** | Patch/allow/add constraints without code changes | `constraint_overrides.yaml` |
| **Layer 3: Shape rules** | Declarative path-based constraints with severity, priority, suggestions | `shapes.yaml` |

### Shape rule example

```yaml
- id: shape:sensitive_data
  name: Sensitive Data Protection
  description: "Operating on CodeEntity linked to restricted/confidential data requires review"
  target:
    entry_type: CodeEntity
    operation: UPDATE
  path: "PROCESSES_DATA -> DataAsset"       # graph traversal
  constraint:
    field: sensitivity
    operator: in
    value: [restricted, confidential]
  severity: block
  priority: 10
  suggestion: |
    This code handles sensitive data. Options:
    (a) Downgrade associated DataAsset sensitivity
    (b) Request temporary exemption (24h TTL)
    (c) Find an alternative that avoids sensitive data
```

A Shape is **data, not code**. The framework reads and evaluates it at runtime. Change a rule → reload YAML → no deployment needed.

### Dual-level approval

```yaml
# approval_policy.yaml — three policy sources, evaluated in order
policies:
  - guard_result       # Shape evaluation result → approval decision
  - action_approval    # Action config flag
  - function_danger    # Function danger_level (read/write/admin)

function_danger:
  auto_approve: [read]
  require_approval: [read_sensitive, write, admin]
```

---

## Core Features

- **Auto Knowledge Graph Construction** — Tree-sitter AST parsing (Python + Java) + LLM semantic extraction → Neo4j or NebulaGraph
- **Incremental Updates** — git diff → change detection → bidirectional BFS impact propagation
- **Concept Alignment** — Four-step aligner (exact → alias → vector → graph structure) solving term drift
- **Shape Constraint Engine** — Declarative YAML rules, graph-path traversal, severity/priority/tag system
- **Approval Gate** — Multi-policy chain, token-based pending, TTL expiry, audit trail
- **Tool Gateway** — Intercepts write operations (Cypher + nGQL dialect-aware), blocks unauthorized mutations
- **Multi-Repo Support** — Repository isolation via VID `repo_id`, per-repo access control
- **Multi-Backend** — Neo4j and NebulaGraph via unified `GraphStore` ABC + factory
- **LangGraph Agent** — ReAct agent with 8 tools for complex multi-step reasoning
- **Butler Engine** — Event-driven perpetual agent (EventBus + Handler + GitWatcher)
- **Web UI** — Vue 3 + TypeScript, cytoscape graph visualization, SSE streaming chat, mermaid flowcharts
- **Production-Ready** — Docker non-root, healthcheck, nginx rate-limiting/HTTPS, Prometheus metrics, structured logging with request_id tracing

---

## Quick Start

```bash
# Clone and install
git clone https://gitee.com/sinxyql/ontology-driven-agent.git
cd ontology-driven-agent
uv sync

# Configure environment
cp .env.example .env
# Edit .env: set graph backend (neo4j|nebula), credentials, LLM API keys

# Build knowledge graph from a repository
uv run ontoagent build ./your-repo --clear

# Query with natural language
uv run ontoagent query "functions handling user authentication"

# Ask the Agent (LangGraph multi-step reasoning)
uv run ontoagent ask "who depends on merge_node()?"

# Start the web UI + API
uv run ontoagent web --port 8000
```

### Docker Deployment

```bash
# Start Neo4j + ChromaDB + OntoAgent
docker compose up -d

# Build graph inside container
docker compose run --rm ontoagent ontoagent build ./repo
```

---

## OntoAgent Schema

### 12 Entities

| Entity | Description |
|--------|-------------|
| `CodeEntity` | function / class / interface / module / file / enum / record / field |
| `ConceptEntity` | business_concept / design_pattern / api_contract / data_model / process |
| `DocEntity` | readme / module_doc / api_doc / comment / wiki / architecture_doc |
| `ResourceEntity` | image / diagram / pdf / config / schema_file / log |
| `ModuleEntity` | Functional clustering results |
| `ChangeSetEntity` | Git commit tracking |
| `ServiceEntity` | External services / APIs |
| `RepositoryEntity` | Multi-repo management (v0.2.0+) |
| `DataAsset` | Business data assets (PII, financial, operational) |
| `ComplianceItem` | Regulatory requirements (GDPR, SOX, PCI-DSS) |
| `CapabilityEntity` | Business capability graph |
| `ProcessEntity` | Business process tracking |

### 29 Relationships

| Category | Relationships |
|----------|--------------|
| Structural (AST) | `CALLS` `EXTENDS` `IMPLEMENTS` `IMPORTS` `CONTAINS` |
| Semantic (LLM) | `SEMANTIC_IMPACT` `DESCRIBES` `ILLUSTRATES` `DERIVED_FROM` |
| Change | `CHANGED_IN` `AFFECTS` `TRIGGERED_BY` |
| Business | `PROCESSES_DATA` `SUBJECT_TO` `GOVERNED_BY` `CALLS_SERVICE` `PUBLISHES_TO` `CONSUMED_BY` |
| Capability | `PRODUCES` `CONSUMES` `COMPOSES_INTO` `REALIZED_BY` `PRECEDES` `EQUIVALENT_TO` |
| Service | `LOGS_FROM` `RUNS_AS` `SERVICE_DEPENDS_ON` |
| Multi-repo | `BELONGS_TO_REPO` `DEPENDS_ON_REPO` |

---

## CLI Commands

```bash
ontoagent build <repo>              # Full build (multi-stage pipeline)
ontoagent query <text>              # Semantic search
ontoagent update <repo> --since <rev>  # Incremental update
ontoagent migrate                   # Schema migration
ontoagent ask <question>            # LangGraph Agent Q&A
ontoagent serve                     # Start MCP server
ontoagent web                       # Start Web API + UI
ontoagent butler serve              # Start event-driven butler engine
ontoagent info                      # System status
```

---

## Project Structure

```
src/ontoagent/
├── domain/            # Schema (12 entities, 29 relations), exceptions, provenance
│   ├── schema.py      #   Entity + relation definitions
│   ├── approval.py    #   Approval domain types
│   └── shapes.py      #   Shape constraint model
├── store/             # GraphStore ABC + Neo4j/NebulaGraph backends + ChromaDB
│   ├── graph_store.py #   Abstract interface
│   ├── neo4j_store.py #   Neo4j implementation
│   ├── nebula_store.py#   NebulaGraph implementation
│   ├── chroma_store.py#   Vector storage
│   ├── factory.py     #   Backend factory (neo4j|nebula)
│   └── migrations/    #   Schema version migrations
├── parsing/           # Tree-sitter parsing (Python + Java + docs)
│   ├── parser/        #   Language-specific parsers
│   └── extractor/     #   Relation + semantic extraction
├── pipeline/          # Build pipeline + constraint config
│   ├── builder.py     #   Multi-stage builder
│   ├── shapes.yaml    #   Layer 3 constraint rules
│   ├── constraints.yaml # Layer 1 traversal paths
│   ├── business_ontology.yaml # Domain ontology config
│   └── ontology_actions.yaml  # Intent → action mapping
├── execution/         # Control layer — constraints + approval
│   ├── action_executor.py  # Orchestrates intent → function
│   ├── shape_evaluator.py  # Evaluates Shape constraints
│   ├── shape_registry.py   # Shape registry
│   ├── decision_fuser.py   # Merges multiple Shape results
│   ├── path_compiler.py    # Compiles path expressions to queries
│   ├── function_runner.py  # Retry + circuit-breaker
│   ├── constraints/        # approval_gate, policies
│   └── functions/          # General + domain functions
├── config/            # Configuration files
│   ├── approval_policy.yaml   # Approval gate policies
│   ├── function_danger_levels.yaml
│   ├── constraint_overrides.yaml # Layer 2 overrides
│   └── tool_gateway.yaml    # Write-operation interception
├── agent/             # LangGraph ReAct Agent
├── butler/            # Event-driven perpetual engine
├── auth/              # Multi-repo ACL + auth middleware
├── observability/     # Metrics + structured logging
├── service/           # Service layer
└── api/               # CLI + MCP server + Web API
```

---

## Tech Stack

| Category | Technology |
|----------|-----------|
| Language | Python 3.13+ |
| Package Manager | uv |
| AST Parsing | Tree-sitter (Python + Java) |
| Graph Database | Neo4j 5.x / NebulaGraph |
| Vector Database | ChromaDB |
| Code Embedding | Qwen2.5-0.5B-Coder (Ollama) |
| Agent Framework | LangGraph |
| Web Framework | FastAPI |
| Frontend | Vue 3 + Vite + TypeScript |
| CLI | Click |
| Quality | ruff + pyright |

---

## Configuration

All config via `.env` or environment variables (see `.env.example`):

| Config | Description |
|--------|-------------|
| `ONTOAGENT_NEO4J_URI` | Neo4j connection URI |
| `ONTOAGENT_NEBULA_HOST` | NebulaGraph host (when using nebula backend) |
| `ONTOAGENT_GRAPH_BACKEND` | `neo4j` or `nebula` |
| `ONTOAGENT_OLLAMA_URL` | Ollama endpoint for embeddings |
| `ONTOAGENT_SEMANTIC_LLM_PROVIDER` | `ollama` or `openai` for semantic extraction |
| `ONTOAGENT_AGENT_LLM_PROVIDER` | LLM provider for Agent (default: zhipu) |
| `ONTOAGENT_CHROMA_DIR` | ChromaDB storage path |

---

## Project Stats

| Metric | Value |
|--------|-------|
| Version | 0.2.0 |
| Source Code | 113 files, 23K LOC |
| Test Code | 1,885 tests (1,772 unit) |
| Entities | 12 |
| Relationships | 29 |
| Commits | 271+ |

---

## License

MPL 2.0

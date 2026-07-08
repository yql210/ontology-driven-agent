# Multi-Domain Validation Report

> **Date**: 2026-07-08
> **Commit**: `bc2c805`
> **Goal**: Prove that OntoAgent's constraint framework is domain-agnostic — changing the domain = changing the ontology definition only, with zero framework code changes.

---

## Pipeline Architecture

```
DDL (.sql)                    OntoAgent Framework (NO code changes)
    │                         ┌──────────────────────────────────────────────────┐
    ▼                         │                                                  │
OntologyAutoGen               │  ontology.json ──► ontology_loader ──► shapes.yaml │
(5-stage pipeline)            │                                  │               │
    │                         │                                  ▼               │
    ▼                         │                          ShapeRegistry           │
ontology.json                 │                                  │               │
                              │                                  ▼               │
                              │              ShapeEvaluator ◄── entity + op      │
                              │                                  │               │
                              │                                  ▼               │
                              │                       DecisionFuser             │
                              │                                  │               │
                              │                                  ▼               │
                              │                     BLOCK / ESCALATE / ALLOW    │
                              └──────────────────────────────────────────────────┘
```

---

## Domain Comparison

| Dimension | Ecommerce | Code Security | Telecom |
|-----------|-----------|---------------|---------|
| **DDL tables** | 7 | — | 9 |
| **Ontology entities** | 64 | 52 | 9 |
| **Ontology relations** | 3 | — | 2 |
| **Shapes generated** | 64 | 52 | 9 |
| **Consistency check** | PASS | PASS | PASS |
| **Entity trigger test** | ✅ 客户→1 shape | ✅ 漏洞→1 shape | ✅ 4 entities→1 shape each |

### Cross-Domain Registry (all loaded simultaneously)

| Registry State | Total Shapes |
|----------------|-------------|
| Base only | 5 |
| Base + Ecommerce | 69 |
| Base + Ecommerce + Code Security | 113 |
| Base + Ecommerce + Code Security + Telecom | **122** |

### Cross-Domain Pre-filtering Test

| Entity | Domain | Evaluated | Triggered | ontology_ref Match | Cross-contamination? |
|--------|--------|-----------|-----------|--------------------|---------------------|
| 客户 | ecommerce | 1 | 1 | {客户} | ✅ None |
| 漏洞 | code_security | 1 | 1 | {漏洞} | ✅ None |
| 电信套餐 | telecom | 1 | 1 | {电信套餐} | ✅ None |
| 通话详单 | telecom | 1 | 1 | {通话详单} | ✅ None |
| 账单 | telecom | 1 | 1 | {账单} | ✅ None |
| 上网流量 | telecom | 1 | 1 | {上网流量} | ✅ None |

---

## Verification Steps

### 1. Unit + Integration Tests
```bash
uv run pytest tests/ -v
# Result: 1588 passed, 0 failed
```

### 2. E2E Approval Lifecycle (Real Neo4j)
```bash
.venv/bin/python tests/e2e/test_approval_lifecycle.py
# Result: 32 passed, 0 failed (12 scenarios)
```

### 3. Ecommerce Domain Generation Chain
```
DDL → OntologyAutoGen → ontology.json (64 entities)
    → ontology_loader → shapes.yaml (64 shapes)
    → ShapeRegistry (69 total)
    → ShapeEvaluator (客户 UPDATE → 1 triggered → ESCALATE)
```

### 4. Telecom Domain Generation Chain (NEW)
```
DDL → OntologyAutoGen → ontology.json (9 entities)
    → ontology_loader → shapes.yaml (9 shapes)
    → ShapeRegistry (14 total)
    → ShapeEvaluator (4/4 telecom entities correctly triggered)
```

### 5. Multi-Domain Coexistence
```
3 domains loaded simultaneously → 122 shapes total
Each domain's entities trigger ONLY their own shapes
Zero cross-contamination
```

---

## Conclusion

**The OntoAgent constraint framework is domain-agnostic.** Adding a new domain (telecom) required:

- ✅ Writing a domain DDL (`telecom.sql`)
- ✅ Writing a domain config (`telecom.yaml`)
- ✅ Running OntologyAutoGen (zero code change)
- ✅ Loading the generated shapes into ShapeRegistry (zero code change)

**Zero lines of framework code were modified to support the telecom domain.**

---

## Competitive Analysis: Alibaba Cloud RDS AI vs OntoAgent

> **Reference**: [阿里云 RDS PostgreSQL 知识图谱-Ontology 能力](https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/knowledge-graph-ontology-capabilities)
> **Date evaluated**: 2026-07-08

### What Alibaba Cloud Built

Alibaba Cloud RDS AI provides a "Database → Ontology → Agent Skill" pipeline:

```
PostgreSQL DDL → LLM auto-modeling → Ontology (ObjectType/LinkType/ActionType)
    → auto-extract entities/relations into knowledge graph
    → LLM generates SKILL.md → install to Claude/Cursor/Gemini/Qoder
    → natural language Q&A → structured analysis report
```

**Core value proposition**: Zero-code, GUI-driven, LLM does all the work. Business analysts can go from a database schema to an AI-queryable knowledge graph without writing any code.

### Pipeline Comparison

| Aspect | Alibaba Cloud RDS AI | OntoAgent |
|--------|---------------------|-----------|
| **Input** | PostgreSQL DDL | Source code (Python/Java AST) + Documents + DDL |
| **Ontology builder** | LLM one-shot inference (non-deterministic, uninterruptible) | OntologyAutoGen 5-stage rule pipeline (deterministic, repeatable) |
| **Storage** | RDS PostgreSQL internal | Neo4j + ChromaDB |
| **Agent interface** | SKILL.md (natural language Skill package) | express_intent + ActionExecutor (programming interface) |
| **Modeling session** | One-shot, interruptible = unrecoverable | Repeatable, re-runnable |
| **Determinism** | ❌ Same DDL may produce different results | ✅ Same input → same output |
| **Version control** | ❌ LLM black box | ✅ ontology.json + shapes.yaml are files |

### Ontology Three-Element Comparison

| Element | Alibaba Cloud | OntoAgent | Assessment |
|---------|--------------|-----------|------------|
| **ObjectType** | LLM infers from DDL, includes PK/attributes/mappings | OntologyAutoGen infers from DDL + code AST + documents | OntoAgent is richer (multi-source) |
| **LinkType** | LLM infers from foreign keys, discovers N:M | OntologyAutoGen infers from FK + code CALLS/EXTENDS/IMPORTS | OntoAgent is richer (code relations) |
| **ActionType** | LLM infers from DDL, embedded in Ontology | Defined in ontology_actions.yaml (manual, domain-agnostic code operations) | Different scope — see below |

**Note on ActionType**: Alibaba Cloud's ActionType represents *business operations* ("查看订单", "分析门店") inferred from business DDL. OntoAgent's Action layer represents *code engineering operations* (refactor, document, extract_interface) that are domain-agnostic. These serve different purposes:
- Alibaba Cloud: business analyst queries (domain-specific, consumer-facing)
- OntoAgent: developer operations (domain-agnostic, governance-facing)

OntoAgent's existing three-layer architecture (Action → Function → Shape) is orthogonal with single-source-of-truth per layer. Adding ActionType to ontology.json would break this clean separation — see architectural analysis below.

### Capability Matrix

| Capability | Alibaba Cloud | OntoAgent | Notes |
|------------|--------------|-----------|-------|
| **DDL → Ontology** | ✅ LLM | ✅ Rule pipeline | Both work; OntoAgent is deterministic |
| **Code AST analysis** | ❌ Database only | ✅ Python + Java | OntoAgent exclusive |
| **Doc → Code linking** | ❌ | ✅ DESCRIBES relation | OntoAgent exclusive |
| **KG visualization** | ✅ Full GUI | ❌ No frontend | Alibaba Cloud advantage |
| **Auto Skill generation** | ✅ SKILL.md → Agent | ❌ express_intent API only | Alibaba Cloud advantage |
| **Constraint system** | ❌ | ✅ 122 shapes, 3 domains | **OntoAgent core moat** |
| **Approval governance** | ❌ | ✅ ApprovalGate + token + audit | **OntoAgent exclusive** |
| **Danger classification** | ❌ | ✅ read/write/admin 4-level | OntoAgent exclusive |
| **Incremental update** | ❌ One-shot modeling | ✅ git diff + bidirectional BFS | OntoAgent exclusive |
| **Multi-domain coexistence** | ❌ Rebuild each time | ✅ ontology_ref pre-filter (116→1) | OntoAgent exclusive |
| **Version-controllable ontology** | ❌ LLM black box | ✅ JSON + YAML files | OntoAgent exclusive |

### Architectural Analysis: Why We Don't Merge ActionType into ontology.json

We analyzed whether to align with Alibaba Cloud by adding `action_types` to `ontology.json`. **Conclusion: No — it would break existing semantics.**

OntoAgent's current three-layer architecture is cleanly orthogonal:

```
ontology.json → "What exists" (entity_types + relations + properties)
ontology_actions.yaml → "What to do" (refactor / document / ...)
functions.yaml → "How to do it" (danger_level + capabilities)
shapes.yaml → "Whether it's allowed" (constraints)
```

Each layer has a **single source of truth**. Adding `action_types` to ontology.json would:

1. **Create dual-source Action definitions** — both `ontology_actions.yaml` and `ontology.json.action_types` define Actions, requiring merge logic and conflict resolution
2. **Inflate Shape count** — ontology_loader would need to generate shapes per (entity × operation) instead of per entity (currently hardcoded to UPDATE)
3. **Generate fake semantics from DDL** — DDL contains no operation information; rules can't infer what doesn't exist (unlike LLM which can hallucinate plausibly)

Alibaba Cloud can put ActionType in its ontology because it has no constraint system — there's nowhere else to put it. OntoAgent already has a dedicated Action layer in the correct architectural position.

### Where They Overlap vs Where We Diverge

**The only intersection**: DDL → Ontology → Knowledge Graph. In this intersection, Alibaba Cloud is stronger on product experience (GUI, zero-code, Skill generation). We acknowledge this honestly.

**Where they don't compete (our moat)**:

| Dimension | Alibaba Cloud | OntoAgent |
|-----------|--------------|-----------|
| **Positioning** | Consumer-side (query data) | Governance-side (constrain operations) |
| **Target user** | Business analyst | Developer |
| **Use case** | "Let me ask questions about my data" | "Is this code/data change safe to execute?" |
| **Safety model** | None (read-only queries) | BLOCK / ESCALATE / ALLOW + approval + audit |
| **Scope** | Database schema only | Code + Documents + Database |

Alibaba Cloud has no constraint system **not because they can't build one**, but because their scenario (business analyst querying data) doesn't need it. However, when the scenario is "AI Agent modifying production code/data", governance becomes essential — and that's OntoAgent's territory.

### Strategic Takeaway

1. **Direction validated**: Alibaba Cloud's investment confirms ontology-driven knowledge graphs are an industry-recognized direction.
2. **Clear differentiation**: Alibaba Cloud owns the consumer-side; OntoAgent owns the governance-side. They are complementary, not directly competitive.
3. **Don't compete on their turf**: We cannot win on "zero-code GUI + Skill generation" and shouldn't try. Focus on constraints, governance, code analysis — capabilities Alibaba Cloud doesn't have and won't build short-term.
4. **Their existence strengthens our narrative**: For grant proposals and papers, Alibaba Cloud serves as evidence that the direction is commercially validated, while OntoAgent's constraint governance represents the unaddressed whitespace.

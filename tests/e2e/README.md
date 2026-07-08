# E2E Approval Lifecycle Tests

End-to-end validation of the complete OntoAgent approval chain on **real Neo4j** with real demo-service + domain ontology data.

## What it tests

32 checks across 12 scenarios:

1. **BLOCK** — sensitive data constraint triggers block
2. **ALLOW** — non-sensitive entity passes through
3. **Function danger** — write-level function → PENDING → approve → execute
4. **Reject** — approval rejected, operation NOT executed
5. **ESCALATE** — ontology shape with priority=0 forces escalation
6. **Full lifecycle** — approve → execute → token consumed
7. **Invalid token** — fake token rejected
8. **Expired token** — TTL-expired token rejected
9. **Entity not found** — graceful error
10. **Unknown intent** — graceful error
11. **Audit log** — pending/resolved/rejected entries recorded
12. **Cross-domain** — ontology_ref pre-filtering prevents cross-contamination

## Prerequisites

1. Neo4j running with demo-service + ecommerce + code-security domain data loaded
2. OntoAgent installed: `uv sync`
3. Environment variables (or `.env`):
   ```
   ONTOAGENT_NEO4J_URI=bolt://...
   ONTOAGENT_NEO4J_USER=neo4j
   ONTOAGENT_NEO4J_PASSWORD=...
   ONTOAGENT_ENABLE_SHAPES=true
   ```

## Run

```bash
# From repo root
.venv/bin/python tests/e2e/test_approval_lifecycle.py

# Or with explicit env
ONTOAGENT_NEO4J_URI=bolt://host:7687 \
ONTOAGENT_NEO4J_PASSWORD=xxx \
.venv/bin/python tests/e2e/test_approval_lifecycle.py
```

Expected output: `32 passed, 0 failed`

"""AlertEntity 相关 Action Function 实现。"""

from __future__ import annotations

from typing import Any


def analyze_by_log_pattern(
    entity_id: str,
    context: dict,
    graph_store: Any,
) -> dict:
    """按日志模式分析 — 只读分析。

    从 graph_store 查找关联 LogEntity，按 pattern 分组统计，
    返回高频模式和可能的根因。

    Args:
        entity_id: AlertEntity ID。
        context: 可选 max_patterns 控制返回数量。
        graph_store: 图存储只读接口。

    Returns:
        {"success": True, "patterns": [...], "root_cause_suggestion": "..."}
    """
    node = graph_store.get_node(entity_id)
    if node is None:
        raise ValueError(f"Entity not found: {entity_id}")

    max_patterns = context.get("max_patterns", 5)

    related_logs = graph_store.query(
        "MATCH (a)-[:TRIGGERED_BY]->(log:LogEntity) WHERE elementId(a) = $eid "
        "RETURN log.pattern AS pattern, log.message AS message, log.level AS level "
        "LIMIT 50",
        {"eid": entity_id},
    )

    if not related_logs:
        related_logs = context.get("related_logs", [])

    pattern_counts: dict[str, list[str]] = {}
    for log in related_logs:
        pattern = log.get("pattern") or "unknown"
        pattern_counts.setdefault(pattern, []).append(log.get("message", ""))

    patterns = sorted(
        [{"pattern": p, "count": len(msgs), "sample": msgs[0]} for p, msgs in pattern_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:max_patterns]

    root_cause = "Unable to determine root cause"
    if patterns:
        top = patterns[0]
        root_cause = f"Most frequent pattern: '{top['pattern']}' ({top['count']} occurrences). Sample: {top['sample']}"

    return {
        "success": True,
        "entity_id": entity_id,
        "patterns": patterns,
        "root_cause_suggestion": root_cause,
        "side_effects": [],
    }


def analyze_by_call_chain(
    entity_id: str,
    context: dict,
    graph_store: Any,
) -> dict:
    """按调用链分析 — 只读分析。

    查找 AlertEntity 关联的 ServiceEntity，再追踪其 CodeEntity 的调用链，
    返回可能的故障传播路径。

    Args:
        entity_id: AlertEntity ID。
        context: 可选 depth 控制追踪深度。
        graph_store: 图存储只读接口。

    Returns:
        {"success": True, "call_chain": [...]}
    """
    node = graph_store.get_node(entity_id)
    if node is None:
        raise ValueError(f"Entity not found: {entity_id}")

    services = graph_store.query(
        "MATCH (a)-[:LOGS_FROM]->(s:ServiceEntity)<-[:RUNS_AS]-(code:CodeEntity) "
        "WHERE elementId(a) = $eid RETURN code.name AS name, s.name AS service LIMIT 10",
        {"eid": entity_id},
    )

    if not services:
        services = context.get("related_services", [])

    call_chain = []
    for svc in services[:5]:
        code_name = svc.get("name", "")
        if code_name:
            chain = graph_store.query(
                "MATCH (caller:CodeEntity)-[:CALLS*1..depth]->(callee:CodeEntity) "
                "WHERE caller.name = $name RETURN callee.name AS callee_name LIMIT 10",
                {"name": code_name},
            )
            call_chain.append(
                {
                    "code": code_name,
                    "service": svc.get("service", ""),
                    "downstream": [c["callee_name"] for c in chain],
                }
            )

    return {
        "success": True,
        "entity_id": entity_id,
        "call_chain": call_chain,
        "side_effects": [],
    }


def find_last_stable(
    entity_id: str,
    context: dict,
    graph_store: Any,
) -> dict:
    """找到最近稳定版本 — 只读分析。

    查找关联的 ChangeSetEntity，按时间倒序返回。

    Args:
        entity_id: AlertEntity ID。
        context: 无特定参数。
        graph_store: 图存储只读接口。

    Returns:
        {"success": True, "recent_changes": [...], "rollback_target": ...}
    """
    node = graph_store.get_node(entity_id)
    if node is None:
        raise ValueError(f"Entity not found: {entity_id}")

    changesets = graph_store.query(
        "MATCH (a)<-[:AFFECTS]-(cs:ChangeSetEntity) "
        "WHERE elementId(a) = $eid "
        "RETURN cs.commit_hash AS hash, cs.message AS msg, cs.committed_at AS at "
        "ORDER BY cs.committed_at DESC LIMIT 5",
        {"eid": entity_id},
    )

    return {
        "success": True,
        "entity_id": entity_id,
        "recent_changes": changesets,
        "rollback_target": changesets[1] if len(changesets) > 1 else None,
        "side_effects": [],
    }


def create_ticket(
    entity_id: str,
    context: dict,
    graph_store: Any,
) -> dict:
    """创建工单 — Phase 3 空壳（需要外部系统集成）。

    Args:
        entity_id: AlertEntity ID。
        context: 无特定参数。
        graph_store: 图存储接口（未使用）。

    Returns:
        空壳响应，含 ticket URL。
    """
    return {
        "success": True,
        "entity_id": entity_id,
        "ticket_url": f"https://tickets.example.com/new?alert={entity_id}",
        "message": "Ticket creation is a stub. Integrate with external ticket system in Phase 4.",
        "side_effects": ["ticket_created"],
    }

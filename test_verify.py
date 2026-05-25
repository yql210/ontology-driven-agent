"""Guava mini 构建结果验证脚本"""
from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore

cfg = LayerKGConfig.from_env()
store = Neo4jGraphStore(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password)

ISSUES = []

with store._driver.session() as s:
    # 1. Entity Type 分布
    print("=" * 60)
    print("1. Entity Type Distribution")
    print("=" * 60)
    r = s.run("MATCH (n:CodeEntity) RETURN n.entity_type AS type, count(*) AS cnt ORDER BY cnt DESC")
    type_counts = {}
    for rec in r:
        t, c = rec["type"], rec["cnt"]
        type_counts[t] = c
        print(f"  {t}: {c}")

    # 2. 关系分布
    print("\n" + "=" * 60)
    print("2. Relation Distribution")
    print("=" * 60)
    rels = s.run("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS cnt ORDER BY cnt DESC")
    rel_counts = {}
    for rec in rels:
        t, c = rec["type"], rec["cnt"]
        rel_counts[t] = c
        print(f"  {t}: {c}")
    rel_total = s.run("MATCH ()-[r]->() RETURN count(*) AS c").single()["c"]
    print(f"  TOTAL: {rel_total}")

    # 3. 关键类存在性检查
    print("\n" + "=" * 60)
    print("3. Key Class Existence Check")
    print("=" * 60)
    key_classes = [
        "Escaper", "Escapers", "CharEscaper", "HtmlEscaper", "UrlEscaper",
        "ArrayBasedEscaperMap", "UnicodeEscaper", "HashCode", "BloomFilter",
        "MediaType", "HostAndPort", "InetAddresses", "Ascii",
        "EscaperBuilder", "PercentEscaper"
    ]
    for cls_name in key_classes:
        r = s.run(
            "MATCH (n:CodeEntity {entity_type: 'class', name: $name}) RETURN n.file_path AS path",
            name=cls_name
        )
        records = list(r)
        if records:
            print(f"  ✅ {cls_name} -> {records[0]['path']}")
        else:
            # Try any entity_type
            r2 = s.run(
                "MATCH (n:CodeEntity {name: $name}) RETURN n.entity_type AS type, n.file_path AS path",
                name=cls_name
            )
            recs2 = list(r2)
            if recs2:
                print(f"  ⚠️  {cls_name} found as {recs2[0]['type']} -> {recs2[0]['path']}")
            else:
                print(f"  ❌ {cls_name} NOT FOUND")
                ISSUES.append(f"Key class missing: {cls_name}")

    # 4. Extends 关系验证
    print("\n" + "=" * 60)
    print("4. Extends Relationship Check")
    print("=" * 60)
    extends_checks = [
        ("HtmlEscaper", "UnicodeEscaper"),
        ("PercentEscaper", "UnicodeEscaper"),
        ("ArrayBasedCharEscaper", "UnicodeEscaper"),
    ]
    for child, parent in extends_checks:
        r = s.run("""
            MATCH (c:CodeEntity {name: $child})-[r:EXTENDS]->(p:CodeEntity {name: $parent})
            RETURN c.name AS cn, p.name AS pn
        """, child=child, parent=parent)
        if list(r):
            print(f"  ✅ {child} extends {parent}")
        else:
            # Check if they exist at all
            r2 = s.run("MATCH (n:CodeEntity {name: $name}) RETURN n.name", name=child)
            r3 = s.run("MATCH (n:CodeEntity {name: $name}) RETURN n.name", name=parent)
            c_exists = bool(list(r2))
            p_exists = bool(list(r3))
            if c_exists and p_exists:
                print(f"  ❌ {child} NOT extends {parent} (both exist but relation missing)")
                ISSUES.append(f"Missing extends: {child} -> {parent}")
            else:
                print(f"  ⚠️  {child} extends {parent} - child exists:{c_exists}, parent exists:{p_exists}")

    # 5. HtmlEscaper 详细验证
    print("\n" + "=" * 60)
    print("5. HtmlEscaper Detailed Check")
    print("=" * 60)
    r = s.run("MATCH (n:CodeEntity {name: 'HtmlEscaper'}) RETURN n.entity_type AS type, n.file_path AS path, n.start_line AS line")
    for rec in r:
        print(f"  Type: {rec['type']}, Path: {rec['path']}, Line: {rec['line']}")

    # Methods of HtmlEscaper
    r = s.run("""
        MATCH (c:CodeEntity {name: 'HtmlEscaper'})<-[:CONTAINS]-(file:CodeEntity)
        MATCH (file)-[:CONTAINS]->(m:CodeEntity {entity_type: 'function'})
        WHERE m.name STARTS WITH 'HtmlEscaper.'
        RETURN m.name AS method
        ORDER BY m.name
    """)
    methods = [rec["method"] for rec in r]
    print(f"  Methods via CONTAINS from file: {methods}")

    # Alternative: direct contains
    r = s.run("""
        MATCH (c:CodeEntity {name: 'HtmlEscaper'})-[:CONTAINS]->(m:CodeEntity {entity_type: 'function'})
        RETURN m.name AS method ORDER BY m.name
    """)
    methods2 = [rec["method"] for rec in r]
    print(f"  Direct CONTAINS children: {methods2}")

    # Fields
    r = s.run("""
        MATCH (c:CodeEntity {name: 'HtmlEscaper'})-[:CONTAINS]->(f:CodeEntity {entity_type: 'field'})
        RETURN f.name AS field ORDER BY f.name
    """)
    fields = [rec["field"] for rec in r]
    print(f"  Fields: {fields}")

    # 6. 数据质量检查
    print("\n" + "=" * 60)
    print("6. Data Quality Check")
    print("=" * 60)

    # 空name节点
    r = s.run("MATCH (n:CodeEntity) WHERE n.name IS NULL OR n.name = '' RETURN count(*) AS c")
    empty_names = r.single()["c"]
    print(f"  Empty name nodes: {empty_names}")
    if empty_names > 0:
        ISSUES.append(f"Empty name nodes: {empty_names}")

    # 重复节点
    r = s.run("""
        MATCH (n:CodeEntity)
        WITH n.name AS name, n.entity_type AS type, n.file_path AS path, count(*) AS cnt
        WHERE cnt > 1
        RETURN name, type, path, cnt
        ORDER BY cnt DESC
        LIMIT 10
    """)
    dupes = list(r)
    if dupes:
        print(f"  ⚠️  Duplicate nodes found: {len(dupes)} groups")
        for d in dupes:
            print(f"    {d['name']} ({d['type']}) @ {d['path']} x{d['cnt']}")
            ISSUES.append(f"Duplicate: {d['name']} ({d['type']}) @ {d['path']} x{d['cnt']}")
    else:
        print("  ✅ No duplicate nodes")

    # External modules
    print("\n  External Modules (top 20):")
    r = s.run("""
        MATCH (n:ExternalModule) RETURN n.name AS name, count{(match)-[:IMPORTS]->(n)} AS refs
        ORDER BY refs DESC LIMIT 20
    """)
    # Try alternative
    r = s.run("""
        MATCH (n) WHERE n:ExternalModule OR labels(n) = ['ExternalModule']
        RETURN n.name AS name
        ORDER BY n.name LIMIT 20
    """)
    for rec in r:
        print(f"    {rec['name']}")

    # 7. Calls 关系抽样
    print("\n" + "=" * 60)
    print("7. Calls Relationship Sample")
    print("=" * 60)
    r = s.run("""
        MATCH (a:CodeEntity)-[:CALLS]->(b:CodeEntity)
        RETURN a.name AS caller, b.name AS callee
        LIMIT 15
    """)
    for rec in r:
        print(f"  {rec['caller']} -> {rec['callee']}")

    # 8. Implements 关系
    print("\n" + "=" * 60)
    print("8. Implements Relationship")
    print("=" * 60)
    r = s.run("""
        MATCH (c:CodeEntity)-[:IMPLEMENTS]->(i:CodeEntity {entity_type: 'interface'})
        RETURN c.name AS cls, i.name AS iface
        ORDER BY iface, cls
    """)
    for rec in r:
        print(f"  {rec['cls']} implements {rec['iface']}")

# Summary
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"Entity types: {type_counts}")
print(f"Relation types: {rel_counts}")
print(f"Total nodes: {sum(type_counts.values())}")
print(f"Total relations: {rel_total}")
print(f"\nIssues found: {len(ISSUES)}")
for issue in ISSUES:
    print(f"  ❌ {issue}")
if not ISSUES:
    print("  ✅ No critical issues found")

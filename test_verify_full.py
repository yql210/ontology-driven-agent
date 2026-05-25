"""Guava 全量构建验证脚本"""
from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore

cfg = LayerKGConfig.from_env()
store = Neo4jGraphStore(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password)

ISSUES = []

with store._driver.session() as s:
    # 1. 总体统计
    print("=" * 60)
    print("1. OVERALL STATISTICS")
    print("=" * 60)
    total = s.run("MATCH (n) RETURN count(*) AS c").single()["c"]
    print(f"Total nodes: {total}")
    
    r = s.run("MATCH (n:CodeEntity) RETURN n.entity_type AS type, count(*) AS cnt ORDER BY cnt DESC")
    type_counts = {}
    for rec in r:
        type_counts[rec["type"]] = rec["cnt"]
        print(f"  {rec['type']}: {rec['cnt']}")
    
    rels = s.run("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS cnt ORDER BY cnt DESC")
    rel_total = 0
    for rec in rels:
        rel_total += rec["cnt"]
        print(f"  REL:{rec['type']}: {rec['cnt']}")
    print(f"Total relations: {rel_total}")

    # 2. 关键类/接口验证
    print("\n" + "=" * 60)
    print("2. KEY CLASS/INTERFACE VERIFICATION")
    print("=" * 60)
    checks = [
        ("ImmutableList", "class"),
        ("ImmutableMap", "class"),
        ("ImmutableSet", "class"),
        ("ArrayList", "class"),
        ("HashMap", "class"),
        ("LinkedList", "class"),
        ("TreeMap", "class"),
        ("ConcurrentHashMap", "class"),
        ("Optional", "class"),
        ("Preconditions", "class"),
        ("Splitter", "class"),
        ("Joiner", "class"),
        ("Lists", "class"),
        ("Maps", "class"),
        ("Sets", "class"),
        ("Iterables", "class"),
        ("Iterators", "class"),
        ("FluentIterable", "class"),
        ("Function", "interface"),
        ("Predicate", "interface"),
        ("Supplier", "interface"),
        ("Cache", "interface"),
        ("LoadingCache", "interface"),
        ("CacheLoader", "class"),
        ("BloomFilter", "class"),
        ("HashFunction", "interface"),
        ("Hashing", "class"),
        ("MediaType", "class"),
        ("Escaper", "class"),
        ("RateLimiter", "class"),
    ]
    found = 0
    missing = []
    for name, etype in checks:
        r = s.run(
            "MATCH (n:CodeEntity {name: $name, entity_type: $etype}) RETURN n.file_path AS path LIMIT 1",
            name=name, etype=etype
        )
        recs = list(r)
        if recs:
            found += 1
            path = recs[0]["path"].split("guava-test/")[-1] if "guava-test/" in recs[0]["path"] else recs[0]["path"]
            print(f"  ✅ {name} ({etype}) @ {path}")
        else:
            missing.append(name)
            print(f"  ❌ {name} ({etype}) NOT FOUND")
    
    print(f"\nFound: {found}/{len(checks)} ({found*100//len(checks)}%)")

    # 3. 继承关系验证
    print("\n" + "=" * 60)
    print("3. EXTENDS RELATIONSHIP SAMPLE")
    print("=" * 60)
    extends_checks = [
        ("ArrayList", "AbstractList"),
        ("HashMap", "AbstractMap"),
        ("ImmutableList", "ImmutableCollection"),
        ("ImmutableMap", "ImmutableMap"),
        ("LinkedHashMap", "HashMap"),
        ("TreeMap", "AbstractMap"),
        ("ConcurrentHashMap", "AbstractMap"),
    ]
    extends_ok = 0
    for child, parent in extends_checks:
        r = s.run("""
            MATCH (c:CodeEntity {name: $child})-[r:EXTENDS]->(p:CodeEntity {name: $parent})
            RETURN c.name AS cn, p.name AS pn LIMIT 1
        """, child=child, parent=parent)
        if list(r):
            extends_ok += 1
            print(f"  ✅ {child} extends {parent}")
        else:
            print(f"  ❌ {child} extends {parent} - NOT FOUND")
    
    # All extends
    r = s.run("MATCH ()-[r:EXTENDS]->() RETURN count(*) AS c")
    total_extends = r.single()["c"]
    print(f"\nTotal EXTENDS relations: {total_extends}")

    # 4. Implements 关系
    print("\n" + "=" * 60)
    print("4. IMPLEMENTS RELATIONSHIP SAMPLE")
    print("=" * 60)
    r = s.run("""
        MATCH (c:CodeEntity)-[:IMPLEMENTS]->(i:CodeEntity {entity_type: 'interface'})
        RETURN c.name AS cls, i.name AS iface
        ORDER BY iface, cls LIMIT 20
    """)
    for rec in r:
        print(f"  {rec['cls']} implements {rec['iface']}")
    r = s.run("MATCH ()-[r:IMPLEMENTS]->() RETURN count(*) AS c")
    print(f"Total IMPLEMENTS: {r.single()['c']}")

    # 5. CONTAINS 关系抽样
    print("\n" + "=" * 60)
    print("5. CONTAINS - Class Method Count Sample")
    print("=" * 60)
    # 看看几个知名类有多少方法
    for cls_name in ["ImmutableList", "ImmutableMap", "Splitter", "Joiner", "Preconditions"]:
        r = s.run("""
            MATCH (c:CodeEntity {name: $name, entity_type: 'class'})-[:CONTAINS]->(m:CodeEntity {entity_type: 'function'})
            RETURN count(*) AS cnt
        """, name=cls_name)
        cnt = r.single()["cnt"]
        print(f"  {cls_name}: {cnt} methods via CONTAINS")

    # 6. 数据质量
    print("\n" + "=" * 60)
    print("6. DATA QUALITY")
    print("=" * 60)
    
    # Empty names
    r = s.run("MATCH (n:CodeEntity) WHERE n.name IS NULL OR n.name = '' RETURN count(*) AS c")
    print(f"  Empty name nodes: {r.single()['c']}")
    
    # Duplicate function names (overloads)
    r = s.run("""
        MATCH (n:CodeEntity {entity_type: 'function'})
        WITH n.name AS name, n.file_path AS path, count(*) AS cnt
        WHERE cnt > 1
        RETURN count(*) AS groups, sum(cnt) AS total
    """)
    rec = r.single()
    print(f"  Overloaded functions (same name+path): {rec['groups']} groups, {rec['total']} total nodes")
    
    # Unique function names
    r = s.run("MATCH (n:CodeEntity {entity_type: 'function'}) RETURN count(DISTINCT n.name) AS unique, count(*) AS total")
    rec = r.single()
    print(f"  Function names: {rec['unique']} unique / {rec['total']} total")
    
    # Duplicate check (name+type+path+start_line)
    r = s.run("""
        MATCH (n:CodeEntity)
        WITH n.name AS name, n.entity_type AS type, n.file_path AS path, n.start_line AS line, count(*) AS cnt
        WHERE cnt > 1 AND line IS NOT NULL
        RETURN count(*) AS true_dupes
    """)
    true_dupes = r.single()["true_dupes"]
    print(f"  True duplicates (same name+type+path+line): {true_dupes}")

    # 7. 外部引用
    print("\n" + "=" * 60)
    print("7. EXTERNAL IMPORTS")
    print("=" * 60)
    r = s.run("""
        MATCH (n) WHERE n:ExternalModule OR any(l IN labels(n) WHERE l CONTAINS 'External')
        RETURN labels(n) AS labels, n.name AS name LIMIT 10
    """)
    for rec in r:
        print(f"  {rec['labels']} {rec['name']}")
    
    # Count unresolved imports
    r = s.run("MATCH ()-[r:IMPORTS]->(n) WHERE n.name STARTS WITH '__ext' RETURN count(*) AS c")
    ext_imports = r.single()["c"]
    print(f"  External import relations: {ext_imports}")

# Summary
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"Total nodes: {total}")
print(f"Entity types: {type_counts}")
print(f"Total relations: {rel_total}")
print(f"Key classes found: {found}/{len(checks)}")
print(f"Extends verified: {extends_ok}/{len(extends_checks)}")
print(f"Issues: {len(ISSUES)}")
for i in ISSUES:
    print(f"  ❌ {i}")

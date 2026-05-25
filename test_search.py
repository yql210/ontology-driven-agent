"""测试语义搜索功能 - 使用 ChromaStore"""
from layerkg.config import LayerKGConfig
from layerkg.chroma_store import ChromaStore

cfg = LayerKGConfig.from_env()
store = ChromaStore(
    persist_dir=cfg.chroma_persist_dir,
    ollama_url=cfg.ollama_base_url,
    embedding_model=cfg.embedding_model,
)

# 检查ChromaDB数据量
print("=== ChromaDB Status ===")
print(f"Collection count: {store._collection.count()}")

# 测试查询
queries = [
    "escape HTML special characters",
    "compute hash code",
    "parse media type",
    "internet address validation",
    "Bloom filter implementation",
]

print("\n=== Semantic Search Tests ===")
for query in queries:
    print(f"\nQuery: '{query}'")
    try:
        results = store.search(query, n_results=5)
        if not results:
            print("  (no results)")
        for i, r in enumerate(results):
            name = r.get("metadata", {}).get("name", r.get("id", "?"))
            etype = r.get("metadata", {}).get("entity_type", "?")
            dist = r.get("distance", 0)
            path = r.get("metadata", {}).get("file_path", "")
            if path:
                path = path.split("guava-mini/")[-1] if "guava-mini/" in path else path.split("guava-test/")[-1]
            print(f"  {i+1}. [{etype}] {name} (dist:{dist:.4f}) @ {path}")
    except Exception as e:
        print(f"  ❌ Error: {e}")

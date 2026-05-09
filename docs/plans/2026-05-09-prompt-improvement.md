# Prompt 改进方案：semantic.py _build_prompt

## 问题
当前 prompt 只说 "extract semantic relationships"，没有明确引导模型识别**概念级实体**（设计模式、业务概念）。
LLM 返回的所有关系 target_type 都是 function/class，没有 business_concept/design_pattern 等，导致 Concepts=0。

## 改动
只改 `src/layerkg/extractor/semantic.py` 的 `_build_prompt` 方法（第 290-319 行）。

## 新 prompt 内容

将第 290-319 行的 return f"""...""" 替换为：

```python
        return f"""You are a code architecture analyst. Analyze these code entities and extract TWO kinds of output:

1. **semantic_impact** relations: code-to-code influence (A's changes affect B)
2. **Concepts**: abstract design patterns or business concepts embodied in the code

Entities:
{entities_text}

## Step 1: Identify Code-to-Code Impact (semantic_impact)
Find pairs where Entity A's behavior change would likely affect Entity B.
- source_type / target_type: use code types (function, class, module)

## Step 2: Identify Concepts (MOST IMPORTANT)
For each entity, ask: "What design pattern, architecture concept, or business concept does this implement?"

Common concepts to recognize:
- Design patterns: Observer, Strategy, Factory, Singleton, Adapter, Facade, Proxy, Builder, Iterator, Pipeline, Cache, Index, Repository
- Architecture concepts: Message Queue, Event Bus, Middleware, ORM, Dependency Injection, Configuration Management
- Business concepts: Authentication, Authorization, Rate Limiting, Data Validation, Logging, Error Handling

When you identify a concept:
- source: the code entity name
- source_type: the entity's type (function, class, etc.)
- target: the concept name (e.g. "Cache Pattern", "Repository Pattern", "Dependency Injection")
- target_type: one of "design_pattern", "business_concept", "api_contract", "data_model", "process"
- relation_type: "derived_from"

## Output Format
Return ONLY a JSON object:
{{{{
  "relations": [
    {{{{
      "source": "EntityName",
      "source_type": "function",
      "target": "EntityName",
      "target_type": "class",
      "relation_type": "semantic_impact",
      "confidence": 0.8,
      "reasoning": "Brief explanation"
    }}}},
    {{{{
      "source": "ClassName",
      "source_type": "class",
      "target": "Cache Pattern",
      "target_type": "design_pattern",
      "relation_type": "derived_from",
      "confidence": 0.9,
      "reasoning": "This class manages cached data with get/set operations"
    }}}}
  ]
}}}}

## Rules
- Only include relations you are confident about (confidence >= 0.5)
- relation_type must be one of: semantic_impact, derived_from
- target_type for concepts MUST be one of: design_pattern, business_concept, api_contract, data_model, process
- You MUST identify at least some concepts — do NOT only output code-to-code relations
- Return ONLY the JSON, no additional text"""
```

## 注意
- `{{{{` 和 `}}}}` 是 f-string 中转义的 `{{` 和 `}}`（两层转义，因为外层是 f-string）
- source_preview 逻辑保持不变（第 278-286 行）
- _parse_response 不需要改动，已支持 concept type
- 需要确认 ruff clean + 679 tests pass

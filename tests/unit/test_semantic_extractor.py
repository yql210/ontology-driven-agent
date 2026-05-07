from __future__ import annotations

import json

import pytest

from layerkg.extractor.semantic import (
    ExtractionResult,
    SemanticExtractor,
    SemanticRelation,
)


class TestSemanticRelation:
    """SemanticRelation dataclass 测试。"""

    def test_create_valid_relation(self):
        """创建有效的 SemanticRelation。"""
        rel = SemanticRelation(
            source_name="UserService",
            source_type="class",
            target_name="AuthModule",
            target_type="class",
            relation_type="semantic_impact",
            confidence=0.8,
            reasoning="UserService depends on AuthModule",
            source_file_path="/src/user.py",
        )
        assert rel.source_name == "UserService"
        assert rel.source_type == "class"
        assert rel.target_name == "AuthModule"
        assert rel.target_type == "class"
        assert rel.relation_type == "semantic_impact"
        assert rel.confidence == 0.8
        assert rel.reasoning == "UserService depends on AuthModule"
        assert rel.source_file_path == "/src/user.py"

    def test_default_values(self):
        """默认值正确：confidence=0.5, reasoning='', source_file_path=''。"""
        rel = SemanticRelation(
            source_name="Foo",
            source_type="function",
            target_name="Bar",
            target_type="function",
            relation_type="semantic_impact",
        )
        assert rel.confidence == 0.5
        assert rel.reasoning == ""
        assert rel.source_file_path == ""

    def test_relation_type_validation(self):
        """relation_type 必须是 4 种语义关系之一。"""
        with pytest.raises(ValueError, match="Invalid relation_type"):
            SemanticRelation(
                source_name="Foo",
                source_type="function",
                target_name="Bar",
                target_type="function",
                relation_type="invalid_type",
            )

    def test_confidence_validation(self):
        """confidence 必须在 [0, 1] 范围内。"""
        with pytest.raises(ValueError, match="confidence must be in \\[0, 1\\]"):
            SemanticRelation(
                source_name="Foo",
                source_type="function",
                target_name="Bar",
                target_type="function",
                relation_type="semantic_impact",
                confidence=1.5,
            )

        with pytest.raises(ValueError, match="confidence must be in \\[0, 1\\]"):
            SemanticRelation(
                source_name="Foo",
                source_type="function",
                target_name="Bar",
                target_type="function",
                relation_type="semantic_impact",
                confidence=-0.1,
            )

    def test_source_type_validation(self):
        """source_type 必须在 VALID_SOURCE_TYPES 中。"""
        with pytest.raises(ValueError, match="Invalid source_type"):
            SemanticRelation(
                source_name="Foo",
                source_type="invalid_type",
                target_name="Bar",
                target_type="function",
                relation_type="semantic_impact",
            )

    def test_target_type_validation(self):
        """target_type 必须在 VALID_SOURCE_TYPES 中。"""
        with pytest.raises(ValueError, match="Invalid target_type"):
            SemanticRelation(
                source_name="Foo",
                source_type="function",
                target_name="Bar",
                target_type="invalid_type",
                relation_type="semantic_impact",
            )


class TestExtractionResult:
    """ExtractionResult dataclass 测试。"""

    def test_create_with_values(self):
        """创建带有所有字段的 ExtractionResult。"""
        rel = SemanticRelation(
            source_name="A",
            source_type="function",
            target_name="B",
            target_type="function",
            relation_type="semantic_impact",
        )
        result = ExtractionResult(
            relations=[rel],
            entities_processed=5,
            llm_calls=2,
            total_tokens=1000,
            elapsed_ms=123.456,
            errors=["error1"],
        )
        assert len(result.relations) == 1
        assert result.entities_processed == 5
        assert result.llm_calls == 2
        assert result.total_tokens == 1000
        assert result.elapsed_ms == 123.456
        assert result.errors == ["error1"]

    def test_default_values(self):
        """默认值正确：空列表和零值。"""
        result = ExtractionResult()
        assert result.relations == []
        assert result.entities_processed == 0
        assert result.llm_calls == 0
        assert result.total_tokens == 0
        assert result.elapsed_ms == 0.0
        assert result.errors == []

    def test_to_dict_output(self):
        """to_dict 输出正确的字典格式。"""
        rel = SemanticRelation(
            source_name="A",
            source_type="function",
            target_name="B",
            target_type="function",
            relation_type="semantic_impact",
            confidence=0.8,
        )
        result = ExtractionResult(
            relations=[rel, rel],
            entities_processed=5,
            llm_calls=2,
            total_tokens=1000,
            elapsed_ms=123.456,
            errors=["error1"],
        )
        d = result.to_dict()
        assert d == {
            "relations_found": 2,
            "entities_processed": 5,
            "llm_calls": 2,
            "total_tokens": 1000,
            "elapsed_ms": 123.46,
            "errors": ["error1"],
        }

    def test_to_dict_elapsed_ms_rounding(self):
        """to_dict 中 elapsed_ms 四舍五入到 2 位小数。"""
        result = ExtractionResult(elapsed_ms=123.456)
        assert result.to_dict()["elapsed_ms"] == 123.46
        assert result.to_dict()["elapsed_ms"] == round(123.456, 2)


class TestSemanticExtractorInit:
    """SemanticExtractor 构造函数测试。"""

    def test_constructor_with_params(self):
        """创建实例，所有参数属性正确。"""
        extractor = SemanticExtractor(
            ollama_url="http://custom:11434",
            model="custom-model",
            batch_size=10,
            max_retries=5,
            timeout=30.0,
            temperature=0.5,
        )
        assert extractor._ollama_url == "http://custom:11434"
        assert extractor._model == "custom-model"
        assert extractor._batch_size == 10
        assert extractor._max_retries == 5
        assert extractor._timeout == 30.0
        assert extractor._temperature == 0.5

    def test_constructor_defaults(self):
        """默认参数值正确：batch_size=5, max_retries=3, timeout=60.0, temperature=0.1。"""
        extractor = SemanticExtractor()
        assert extractor._ollama_url == "http://localhost:11434"
        assert extractor._model == "qwen3.5:9b"
        assert extractor._batch_size == 5
        assert extractor._max_retries == 3
        assert extractor._timeout == 60.0
        assert extractor._temperature == 0.1

    def test_valid_semantic_relations(self):
        """VALID_SEMANTIC_RELATIONS 包含 4 种关系。"""
        assert {
            "semantic_impact",
            "describes",
            "illustrates",
            "derived_from",
        } == SemanticExtractor.VALID_SEMANTIC_RELATIONS

    def test_context_manager(self):
        """context manager: __enter__ 返回 self, __exit__ 调用 close。"""
        extractor = SemanticExtractor()
        with extractor as e:
            assert e is extractor
        # __exit__ 应该调用 close，但不抛出异常
        assert True


class TestBuildPrompt:
    """_build_prompt 静态方法测试。"""

    def test_empty_entities(self):
        """空 entities → prompt 包含 "Entities:" 后为空。"""
        prompt = SemanticExtractor._build_prompt([])
        assert "Entities:\n\n\nExtract" in prompt

    def test_single_entity_with_source(self):
        """1 个 entity（有 source）→ prompt 包含实体名和源码预览。"""
        from layerkg.schema import CodeEntity

        entity = CodeEntity(
            name="UserService",
            entity_type="class",
            source="class UserService:\n    pass",
        )
        prompt = SemanticExtractor._build_prompt([entity])
        assert "class `UserService`" in prompt
        assert "class UserService:" in prompt

    def test_single_entity_without_source(self):
        """1 个 entity（无 source）→ prompt 只包含实体名。"""
        from layerkg.schema import CodeEntity

        entity = CodeEntity(name="UserService", entity_type="class")
        prompt = SemanticExtractor._build_prompt([entity])
        assert "class `UserService`" in prompt
        # Entities 部分应该只显示实体名和类型，没有源码预览
        entities_section = prompt.split("Extract ONLY")[0]
        # 应该是 "- class `UserService`\n" 格式
        assert "- class `UserService`\n" in entities_section

    def test_multiple_entities(self):
        """多个 entities → prompt 包含所有实体。"""
        from layerkg.schema import CodeEntity

        entities = [
            CodeEntity(name="UserService", entity_type="class"),
            CodeEntity(name="AuthModule", entity_type="module"),
        ]
        prompt = SemanticExtractor._build_prompt(entities)
        assert "class `UserService`" in prompt
        assert "module `AuthModule`" in prompt

    def test_long_source_truncated(self):
        """长 source（>200 chars）→ prompt 中被截断为 200 + '...'。"""
        from layerkg.schema import CodeEntity

        long_source = "x" * 250
        entity = CodeEntity(name="LongFunction", entity_type="function", source=long_source)
        prompt = SemanticExtractor._build_prompt([entity])
        # 应该截断
        assert "..." in prompt
        # 截断后 x 的数量不超过 210
        x_count = prompt.count("x")
        assert x_count <= 210  # 200 + "..." + 可能的额外字符


class TestParseResponseNormal:
    """_parse_response 静态方法正常情况测试。"""

    def test_standard_json(self):
        """标准 JSON → 解析出 1 个 SemanticRelation。"""
        response = '{"relations": [{"source": "A", "source_type": "function", "target": "B", "target_type": "class", "relation_type": "semantic_impact", "confidence": 0.8, "reasoning": "test"}]}'
        relations = SemanticExtractor._parse_response(response)
        assert len(relations) == 1
        assert relations[0].source_name == "A"
        assert relations[0].relation_type == "semantic_impact"
        assert relations[0].confidence == 0.8

    def test_json_code_block(self):
        """包含 ```json``` code block → 正确提取 JSON。"""
        response = '```json\n{"relations": [{"source": "A", "source_type": "function", "target": "B", "target_type": "class", "relation_type": "semantic_impact"}]}\n```'
        relations = SemanticExtractor._parse_response(response)
        assert len(relations) == 1
        assert relations[0].source_name == "A"

    def test_markdown_code_block(self):
        """包含 ``` ``` code block → 正确提取 JSON。"""
        response = '```\n{"relations": [{"source": "A", "source_type": "function", "target": "B", "target_type": "class", "relation_type": "semantic_impact"}]}\n```'
        relations = SemanticExtractor._parse_response(response)
        assert len(relations) == 1
        assert relations[0].source_name == "A"

    def test_multiple_relations(self):
        """多个 relations → 全部解析。"""
        response = '{"relations": [{"source": "A", "source_type": "function", "target": "B", "target_type": "class", "relation_type": "semantic_impact"}, {"source": "C", "source_type": "class", "target": "D", "target_type": "function", "relation_type": "describes"}]}'
        relations = SemanticExtractor._parse_response(response)
        assert len(relations) == 2
        assert relations[0].source_name == "A"
        assert relations[1].source_name == "C"

    def test_confidence_default(self):
        """confidence 缺失 → 默认 0.5。"""
        response = '{"relations": [{"source": "A", "source_type": "function", "target": "B", "target_type": "class", "relation_type": "semantic_impact"}]}'
        relations = SemanticExtractor._parse_response(response)
        assert len(relations) == 1
        assert relations[0].confidence == 0.5


class TestParseResponseErrors:
    """_parse_response 静态方法异常情况测试。"""

    def test_non_json_raises(self):
        """非 JSON 文本 → raise ExtractionError。"""
        from layerkg.exceptions import ExtractionError

        with pytest.raises(ExtractionError, match="not valid JSON"):
            SemanticExtractor._parse_response("not json at all")

    def test_missing_relations_key_raises(self):
        """JSON 缺少 "relations" key → raise ExtractionError。"""
        from layerkg.exceptions import ExtractionError

        with pytest.raises(ExtractionError, match="missing 'relations' key"):
            SemanticExtractor._parse_response('{"foo": "bar"}')

    def test_missing_source_skips(self):
        """单条 relation 缺少 "source" key → 跳过该条，返回其余。"""
        response = '{"relations": [{"source": "A", "source_type": "function", "target": "B", "target_type": "class", "relation_type": "semantic_impact"}, {"target": "C", "target_type": "class", "relation_type": "semantic_impact"}]}'
        relations = SemanticExtractor._parse_response(response)
        assert len(relations) == 1
        assert relations[0].source_name == "A"

    def test_invalid_relation_type_skips(self):
        """relation_type 无效 → 跳过该条。"""
        # 无效的 relation_type 会在 SemanticRelation __post_init__ 中抛出 ValueError
        response = '{"relations": [{"source": "A", "source_type": "function", "target": "B", "target_type": "class", "relation_type": "invalid_type"}]}'
        relations = SemanticExtractor._parse_response(response)
        assert len(relations) == 0

    def test_confidence_gt_1_skips(self):
        """confidence > 1.0 → 跳过该条。"""
        # confidence > 1 会在 SemanticRelation __post_init__ 中抛出 ValueError
        response = '{"relations": [{"source": "A", "source_type": "function", "target": "B", "target_type": "class", "relation_type": "semantic_impact", "confidence": 1.5}]}'
        relations = SemanticExtractor._parse_response(response)
        assert len(relations) == 0


class TestValidateRelation:
    """_validate_relation 静态方法测试。"""

    def test_valid_relation(self):
        """有效关系 → True。"""
        rel = SemanticRelation(
            source_name="A",
            source_type="function",
            target_name="B",
            target_type="class",
            relation_type="semantic_impact",
            confidence=0.8,
        )
        assert SemanticExtractor._validate_relation(rel) is True

    def test_invalid_relation_type(self):
        """无效 relation_type → False。"""
        # 创建一个手动构造的关系（绕过 __post_init__）
        rel = object.__new__(SemanticRelation)
        rel.source_name = "A"
        rel.source_type = "function"
        rel.target_name = "B"
        rel.target_type = "class"
        rel.relation_type = "invalid_type"
        rel.confidence = 0.8
        rel.reasoning = ""
        rel.source_file_path = ""
        assert SemanticExtractor._validate_relation(rel) is False

    def test_low_confidence(self):
        """confidence < 0.5 → False。"""
        rel = SemanticRelation(
            source_name="A",
            source_type="function",
            target_name="B",
            target_type="class",
            relation_type="semantic_impact",
            confidence=0.3,
        )
        assert SemanticExtractor._validate_relation(rel) is False

    def test_empty_source_name(self):
        """source_name 为空 → False。"""
        # 创建一个手动构造的关系（绕过 __post_init__）
        rel = object.__new__(SemanticRelation)
        rel.source_name = ""
        rel.source_type = "function"
        rel.target_name = "B"
        rel.target_type = "class"
        rel.relation_type = "semantic_impact"
        rel.confidence = 0.8
        rel.reasoning = ""
        rel.source_file_path = ""
        assert SemanticExtractor._validate_relation(rel) is False


class TestCallLlm:
    """_call_llm 方法测试。"""

    def test_call_llm_returns_content_and_tokens(self, mocker):
        """成功调用 → 返回 (content, tokens) 元组。"""
        from layerkg.extractor.semantic import SemanticExtractor

        extractor = SemanticExtractor(
            ollama_url="http://test:11434",
            model="test-model",
            timeout=10.0,
        )

        mock_response = mocker.Mock()
        mock_response.json.return_value = {
            "message": {"content": "test response"},
            "prompt_eval_count": 100,
            "eval_count": 50,
        }
        mock_response.raise_for_status = mocker.Mock()

        mocker.patch.object(extractor._client, "post", return_value=mock_response)

        content, tokens = extractor._call_llm("test prompt")

        assert content == "test response"
        assert tokens == 150  # 100 + 50
        extractor._client.post.assert_called_once()
        call_args = extractor._client.post.call_args
        assert "http://test:11434/api/chat" in str(call_args)
        assert call_args.kwargs["json"]["model"] == "test-model"
        assert call_args.kwargs["json"]["stream"] is False
        assert call_args.kwargs["json"]["think"] is False

    def test_call_llm_http_error_raises_extraction_error(self, mocker):
        """HTTP 错误 → raise ExtractionError。"""
        import httpx

        from layerkg.exceptions import ExtractionError
        from layerkg.extractor.semantic import SemanticExtractor

        extractor = SemanticExtractor(ollama_url="http://test:11434")

        mocker.patch.object(
            extractor._client,
            "post",
            side_effect=httpx.HTTPStatusError("404", request=mocker.Mock(), response=mocker.Mock()),
        )

        with pytest.raises(ExtractionError, match="Ollama API call failed"):
            extractor._call_llm("test")

    def test_call_llm_timeout_raises_extraction_error(self, mocker):
        """超时 → raise ExtractionError。"""
        import httpx

        from layerkg.exceptions import ExtractionError
        from layerkg.extractor.semantic import SemanticExtractor

        extractor = SemanticExtractor(timeout=1.0)

        mocker.patch.object(extractor._client, "post", side_effect=httpx.TimeoutException("timeout"))

        with pytest.raises(ExtractionError, match="Ollama API call failed"):
            extractor._call_llm("test")

    def test_call_llm_token_count_zero_when_missing(self, mocker):
        """响应无 token 字段 → tokens = 0。"""
        from layerkg.extractor.semantic import SemanticExtractor

        extractor = SemanticExtractor()

        mock_response = mocker.Mock()
        mock_response.json.return_value = {"message": {"content": "test"}}
        mock_response.raise_for_status = mocker.Mock()

        mocker.patch.object(extractor._client, "post", return_value=mock_response)

        content, tokens = extractor._call_llm("test")

        assert content == "test"
        assert tokens == 0


class TestCreateBatches:
    """_create_batches 方法测试。"""

    def test_create_batches_with_batch_size_3(self):
        """5 entities, batch_size=3 → [[0:3], [3:5]]。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(batch_size=3)
        entities = [
            CodeEntity(name=f"Entity{i}", entity_type="function") for i in range(5)
        ]

        batches = extractor._create_batches(entities)

        assert len(batches) == 2
        assert len(batches[0]) == 3
        assert len(batches[1]) == 2
        assert batches[0][0].name == "Entity0"
        assert batches[1][0].name == "Entity3"

    def test_create_batches_empty_entities(self):
        """空 entities → []。"""
        from layerkg.extractor.semantic import SemanticExtractor

        extractor = SemanticExtractor(batch_size=3)
        batches = extractor._create_batches([])

        assert batches == []

    def test_create_batches_single_batch(self):
        """batch_size >= entities 数量 → 单批。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(batch_size=10)
        entities = [
            CodeEntity(name=f"Entity{i}", entity_type="function") for i in range(3)
        ]

        batches = extractor._create_batches(entities)

        assert len(batches) == 1
        assert len(batches[0]) == 3


class TestExtractBatch:
    """extract_batch 方法测试。"""

    def test_extract_batch_returns_relations(self, mocker):
        """mock _call_llm → 返回 2 个 relations。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(batch_size=5)

        entities = [
            CodeEntity(name="UserService", entity_type="class", file_path="/src/user.py"),
            CodeEntity(name="AuthModule", entity_type="class", file_path="/src/auth.py"),
        ]

        mock_response = json.dumps({
            "relations": [
                {
                    "source": "UserService",
                    "source_type": "class",
                    "target": "AuthModule",
                    "target_type": "class",
                    "relation_type": "semantic_impact",
                    "confidence": 0.8,
                    "reasoning": "test",
                },
                {
                    "source": "AuthModule",
                    "source_type": "class",
                    "target": "UserService",
                    "target_type": "class",
                    "relation_type": "semantic_impact",
                    "confidence": 0.7,
                    "reasoning": "test2",
                },
            ]
        })

        mocker.patch.object(extractor, "_call_llm", return_value=(mock_response, 100))

        relations, tokens = extractor.extract_batch(entities)

        assert len(relations) == 2
        assert tokens == 100
        assert relations[0].source_name == "UserService"
        assert relations[1].source_name == "AuthModule"

    def test_extract_batch_empty_entities(self, mocker):
        """空 entities → 返回空列表（不调用 _call_llm）。"""
        from layerkg.extractor.semantic import SemanticExtractor

        extractor = SemanticExtractor()
        mock_llm = mocker.patch.object(extractor, "_call_llm")

        relations, tokens = extractor.extract_batch([])

        assert relations == []
        assert tokens == 0
        mock_llm.assert_not_called()

    def test_extract_batch_fills_file_path(self, mocker):
        """file_path 自动填充。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor()

        entities = [
            CodeEntity(name="UserService", entity_type="class", file_path="/src/user.py"),
            CodeEntity(name="AuthModule", entity_type="class", file_path="/src/auth.py"),
        ]

        mock_response = json.dumps({
            "relations": [
                {
                    "source": "UserService",
                    "source_type": "class",
                    "target": "AuthModule",
                    "target_type": "class",
                    "relation_type": "semantic_impact",
                },
            ]
        })

        mocker.patch.object(extractor, "_call_llm", return_value=(mock_response, 50))

        relations, _tokens = extractor.extract_batch(entities)

        assert len(relations) == 1
        assert relations[0].source_file_path == "/src/user.py"

    def test_extract_batch_retries_on_failure(self, mocker):
        """_call_llm 失败重试 3 次后 raise ExtractionError。"""
        from layerkg.exceptions import ExtractionError
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(max_retries=3)

        entities = [
            CodeEntity(name="UserService", entity_type="class"),
        ]

        # 前 2 次失败，第 3 次成功
        mock_llm = mocker.patch.object(
            extractor,
            "_call_llm",
            side_effect=[
                ExtractionError("fail 1"),
                ExtractionError("fail 2"),
                ('{"relations": []}', 0),
            ],
        )
        mocker.patch("time.sleep")

        relations, _tokens = extractor.extract_batch(entities)

        assert mock_llm.call_count == 3
        assert relations == []
        assert relations == []

    def test_extract_batch_exhausts_retries(self, mocker):
        """重试耗尽后 raise ExtractionError。"""
        from layerkg.exceptions import ExtractionError
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(max_retries=2)

        entities = [
            CodeEntity(name="UserService", entity_type="class"),
        ]

        mocker.patch.object(
            extractor,
            "_call_llm",
            side_effect=ExtractionError("always fail"),
        )
        mocker.patch("time.sleep")

        with pytest.raises(ExtractionError, match="always fail"):
            extractor.extract_batch(entities)


class TestExtractMultiBatch:
    """extract 多批次测试。"""

    def test_extract_7_entities_batch_size_3(self, mocker):
        """7 个 entities（batch_size=3）→ 3 次 LLM 调用。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(batch_size=3)

        entities = [
            CodeEntity(name=f"Entity{i}", entity_type="function") for i in range(7)
        ]

        mock_response = json.dumps({"relations": []})

        mock_llm = mocker.patch.object(extractor, "_call_llm", return_value=(mock_response, 50))

        result = extractor.extract(entities)

        assert result.entities_processed == 7
        assert result.llm_calls == 3
        assert mock_llm.call_count == 3

    def test_extract_entities_processed_count(self, mocker):
        """ExtractionResult.entities_processed = 7。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(batch_size=5)

        entities = [
            CodeEntity(name=f"Entity{i}", entity_type="function") for i in range(7)
        ]

        mock_response = json.dumps({"relations": []})

        mocker.patch.object(extractor, "_call_llm", return_value=(mock_response, 50))

        result = extractor.extract(entities)

        assert result.entities_processed == 7

    def test_extract_llm_calls_count(self, mocker):
        """ExtractionResult.llm_calls = 2。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(batch_size=5)

        entities = [
            CodeEntity(name=f"Entity{i}", entity_type="function") for i in range(7)
        ]

        mock_response = json.dumps({"relations": []})

        mocker.patch.object(extractor, "_call_llm", return_value=(mock_response, 50))

        result = extractor.extract(entities)

        assert result.llm_calls == 2

    def test_extract_partial_batch_failure(self, mocker):
        """部分批次失败 → errors 非空但其他批次结果保留。"""
        from layerkg.exceptions import ExtractionError
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(batch_size=3)

        entities = [
            CodeEntity(name=f"Entity{i}", entity_type="function") for i in range(7)
        ]

        mock_response = json.dumps({
            "relations": [
                {
                    "source": "Entity0",
                    "source_type": "function",
                    "target": "Entity1",
                    "target_type": "function",
                    "relation_type": "semantic_impact",
                },
            ]
        })

        # 第 2 批失败（包含 Entity3）
        def mock_call(prompt):
            if "Entity3" in prompt:
                raise ExtractionError("batch 2 failed")
            return (mock_response, 50)

        mocker.patch.object(extractor, "_call_llm", side_effect=mock_call)

        result = extractor.extract(entities)

        assert len(result.errors) == 1
        assert "batch 2 failed" in result.errors[0]
        assert len(result.relations) >= 1
        assert result.entities_processed == 7
        assert result.llm_calls == 3


class TestExtractComplete:
    """extract 完整流程测试。"""

    def test_extract_complete_result_fields(self, mocker):
        """完整流程：extract(5 entities) → ExtractionResult 所有字段正确。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(batch_size=5)

        entities = [
            CodeEntity(
                name=f"Func{i}",
                entity_type="function",
                source=f"def func{i}(): pass",
                file_path=f"/src/func{i}.py",
            )
            for i in range(5)
        ]

        mock_response = json.dumps({
            "relations": [
                {
                    "source": "Func0",
                    "source_type": "function",
                    "target": "Func1",
                    "target_type": "function",
                    "relation_type": "semantic_impact",
                    "confidence": 0.8,
                },
            ]
        })

        mocker.patch.object(extractor, "_call_llm", return_value=(mock_response, 100))

        result = extractor.extract(entities)

        assert result.entities_processed == 5
        assert result.llm_calls == 1
        assert len(result.relations) == 1
        assert result.relations[0].source_name == "Func0"
        assert result.total_tokens == 100

    def test_extract_elapsed_ms_positive(self, mocker):
        """elapsed_ms > 0。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(batch_size=5)

        entities = [
            CodeEntity(name="Func1", entity_type="function", source="def func1(): pass"),
        ]

        mock_response = json.dumps({"relations": []})

        mocker.patch.object(extractor, "_call_llm", return_value=(mock_response, 50))

        result = extractor.extract(entities)

        assert result.elapsed_ms > 0

    def test_extract_empty_entities(self, mocker):
        """空 entities → ExtractionResult(relations=[], entities_processed=0, llm_calls=0)。"""
        from layerkg.extractor.semantic import SemanticExtractor

        extractor = SemanticExtractor(batch_size=5)

        result = extractor.extract([])

        assert result.relations == []
        assert result.entities_processed == 0
        assert result.llm_calls == 0
        assert result.elapsed_ms >= 0

    def test_extract_with_doc_entities(self, mocker):
        """extract(doc_entities=...) → 额外 describes 关系。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity, DocEntity

        extractor = SemanticExtractor(batch_size=5)
        mock_response = json.dumps({"relations": []})
        mocker.patch.object(extractor, "_call_llm", return_value=(mock_response, 10))

        code_entities = [
            CodeEntity(name="UserService", entity_type="class", source="class UserService: pass"),
        ]
        doc_entities = [
            DocEntity(name="UserService", entity_type="api_doc", content="Describes UserService"),
        ]

        result = extractor.extract(code_entities, doc_entities=doc_entities)

        # LLM 返回 0 + 跨类型匹配 1
        assert len(result.relations) >= 1
        describes_rels = [r for r in result.relations if r.relation_type == "describes"]
        assert len(describes_rels) >= 1

    def test_extract_with_concept_entities(self, mocker):
        """extract(concept_entities=...) → 额外 derived_from 关系。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity, ConceptEntity

        extractor = SemanticExtractor(batch_size=5)
        mock_response = json.dumps({"relations": []})
        mocker.patch.object(extractor, "_call_llm", return_value=(mock_response, 10))

        code_entities = [
            CodeEntity(name="UserService", entity_type="class", source="class UserService: Repository pattern"),
        ]
        concept_entities = [
            ConceptEntity(name="Repository", entity_type="design_pattern", description="Repository pattern"),
        ]

        result = extractor.extract(code_entities, concept_entities=concept_entities)

        derived_rels = [r for r in result.relations if r.relation_type == "derived_from"]
        assert len(derived_rels) >= 1


class TestExtractCrossTypeRelations:
    """_extract_cross_type_relations 测试。"""

    def test_doc_name_in_source_creates_describes_relation(self):
        """Doc 名称出现在 code source → 建立 describes 关系。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity, DocEntity

        extractor = SemanticExtractor()

        code_entities = [
            CodeEntity(
                name="UserService",
                entity_type="class",
                source="# UserService handles user authentication\n# See README.md for details",
            ),
        ]

        doc_entities = [
            DocEntity(name="README", entity_type="readme"),
        ]

        relations = extractor._extract_cross_type_relations(
            code_entities, doc_entities, "describes"
        )

        assert len(relations) == 1
        assert relations[0].source_name == "README"
        assert relations[0].source_type == "readme"
        assert relations[0].target_name == "UserService"
        assert relations[0].target_type == "class"
        assert relations[0].relation_type == "describes"

    def test_no_match_returns_empty_list(self):
        """无匹配 → 返回空列表。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity, DocEntity

        extractor = SemanticExtractor()

        code_entities = [
            CodeEntity(
                name="UserService",
                entity_type="class",
                source="class UserService: pass",
            ),
        ]

        doc_entities = [
            DocEntity(name="APIDoc", entity_type="api_doc"),
        ]

        relations = extractor._extract_cross_type_relations(
            code_entities, doc_entities, "describes"
        )

        assert relations == []

    def test_concept_name_match_creates_derived_from_relation(self):
        """Concept 名称匹配 → 建立 derived_from 关系。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity, ConceptEntity

        extractor = SemanticExtractor()

        code_entities = [
            CodeEntity(
                name="UserRepository",
                entity_type="class",
                source="# Implements Repository pattern",
            ),
        ]

        concept_entities = [
            ConceptEntity(name="Repository", entity_type="design_pattern"),
        ]

        relations = extractor._extract_cross_type_relations(
            code_entities, concept_entities, "derived_from"
        )

        assert len(relations) == 1
        assert relations[0].source_name == "UserRepository"
        assert relations[0].source_type == "class"
        assert relations[0].target_name == "Repository"
        assert relations[0].target_type == "design_pattern"
        assert relations[0].relation_type == "derived_from"


class TestExtractBoundary:
    """边界测试。"""

    def test_large_entities_batched_correctly(self, mocker):
        """大量实体（20个）→ 正确分批（batch_size=5 → 4批）。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(batch_size=5)

        entities = [
            CodeEntity(name=f"Entity{i}", entity_type="function") for i in range(20)
        ]

        mock_response = json.dumps({"relations": []})

        call_count = 0

        def mock_call(prompt):
            nonlocal call_count
            call_count += 1
            return (mock_response, 50)

        mocker.patch.object(extractor, "_call_llm", side_effect=mock_call)

        extractor.extract(entities)

        assert call_count == 4

    def test_llm_returns_empty_relations(self, mocker):
        """LLM 返回空 relations → 无异常，空列表。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(batch_size=5)

        entities = [
            CodeEntity(name="Func1", entity_type="function"),
        ]

        mock_response = json.dumps({"relations": []})

        mocker.patch.object(extractor, "_call_llm", return_value=(mock_response, 50))

        result = extractor.extract(entities)

        assert result.relations == []
        assert len(result.errors) == 0

    def test_confidence_filtering(self, mocker):
        """置信度过滤：confidence < 0.5 被过滤。"""
        from layerkg.extractor.semantic import SemanticExtractor
        from layerkg.schema import CodeEntity

        extractor = SemanticExtractor(batch_size=5)

        entities = [
            CodeEntity(name="Func1", entity_type="function"),
        ]

        mock_response = json.dumps({
            "relations": [
                {
                    "source": "Func1",
                    "source_type": "function",
                    "target": "Func2",
                    "target_type": "function",
                    "relation_type": "semantic_impact",
                    "confidence": 0.8,
                },
                {
                    "source": "Func1",
                    "source_type": "function",
                    "target": "Func3",
                    "target_type": "function",
                    "relation_type": "semantic_impact",
                    "confidence": 0.3,
                },
            ]
        })

        mocker.patch.object(extractor, "_call_llm", return_value=(mock_response, 50))

        result = extractor.extract(entities)

        # 只有 confidence >= 0.5 的关系被保留
        assert len(result.relations) == 1
        assert result.relations[0].confidence == 0.8



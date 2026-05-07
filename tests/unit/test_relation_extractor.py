from __future__ import annotations

from layerkg.extractor.relation import RelationExtractor
from layerkg.parser.base import ExtractedRelation
from layerkg.schema import CodeEntity


def test_extract_contains_module_to_function() -> None:
    """测试 module→function contains 关系解析。"""
    # Arrange
    entities = [
        CodeEntity(name="mymodule", entity_type="module", id="uuid-1"),
        CodeEntity(name="myfunc", entity_type="function", id="uuid-2"),
    ]
    relations = [
        ExtractedRelation(
            source_name="mymodule",
            source_type="module",
            target_name="myfunc",
            target_type="function",
            relation_type="contains",
            file_path="test.py",
        )
    ]

    extractor = RelationExtractor()
    extractor.add_parse_result(entities, relations)

    # Act
    resolved = extractor.resolve(entities)

    # Assert
    assert len(resolved) == 1
    assert resolved[0].source_id == "uuid-1"
    assert resolved[0].target_id == "uuid-2"
    assert resolved[0].relation_type == "contains"


def test_extract_contains_module_to_class() -> None:
    """测试 module→class contains 关系解析。"""
    # Arrange
    entities = [
        CodeEntity(name="mymodule", entity_type="module", id="uuid-1"),
        CodeEntity(name="MyClass", entity_type="class", id="uuid-2"),
    ]
    relations = [
        ExtractedRelation(
            source_name="mymodule",
            source_type="module",
            target_name="MyClass",
            target_type="class",
            relation_type="contains",
            file_path="test.py",
        )
    ]

    extractor = RelationExtractor()
    extractor.add_parse_result(entities, relations)

    # Act
    resolved = extractor.resolve(entities)

    # Assert
    assert len(resolved) == 1
    assert resolved[0].source_id == "uuid-1"
    assert resolved[0].target_id == "uuid-2"


def test_extract_contains_class_to_method() -> None:
    """测试 class→method contains 关系解析。"""
    # Arrange
    entities = [
        CodeEntity(name="MyClass", entity_type="class", id="uuid-1"),
        CodeEntity(name="MyClass.method", entity_type="function", id="uuid-2"),
    ]
    relations = [
        ExtractedRelation(
            source_name="MyClass",
            source_type="class",
            target_name="MyClass.method",
            target_type="function",
            relation_type="contains",
            file_path="test.py",
        )
    ]

    extractor = RelationExtractor()
    extractor.add_parse_result(entities, relations)

    # Act
    resolved = extractor.resolve(entities)

    # Assert
    assert len(resolved) == 1
    assert resolved[0].source_id == "uuid-1"
    assert resolved[0].target_id == "uuid-2"


def test_extract_extends_single_parent() -> None:
    """测试 Dog extends Animal 继承关系解析。"""
    # Arrange
    entities = [
        CodeEntity(name="Dog", entity_type="class", id="uuid-dog"),
        CodeEntity(name="Animal", entity_type="class", id="uuid-animal"),
    ]
    relations = [
        ExtractedRelation(
            source_name="Dog",
            source_type="class",
            target_name="Animal",
            target_type="class",
            relation_type="extends",
            file_path="test.py",
        )
    ]

    extractor = RelationExtractor()
    extractor.add_parse_result(entities, relations)

    # Act
    resolved = extractor.resolve(entities)

    # Assert
    assert len(resolved) == 1
    assert resolved[0].source_id == "uuid-dog"
    assert resolved[0].target_id == "uuid-animal"
    assert resolved[0].relation_type == "extends"


def test_extract_extends_no_parent() -> None:
    """测试无父类的 extends 关系被过滤。"""
    # Arrange
    entities = [
        CodeEntity(name="Dog", entity_type="class", id="uuid-dog"),
    ]
    relations = [
        ExtractedRelation(
            source_name="Dog",
            source_type="class",
            target_name="Animal",
            target_type="class",
            relation_type="extends",
            file_path="test.py",
        )
    ]

    extractor = RelationExtractor()
    extractor.add_parse_result(entities, relations)

    # Act
    resolved = extractor.resolve(entities)

    # Assert
    assert len(resolved) == 0


def test_extract_empty_input() -> None:
    """测试空输入返回空结果。"""
    # Arrange
    extractor = RelationExtractor()

    # Act
    resolved = extractor.resolve([])

    # Assert
    assert resolved == []


def test_resolve_filters_invalid() -> None:
    """测试无效引用被过滤（源或目标不在实体列表中）。"""
    # Arrange
    entities = [
        CodeEntity(name="A", entity_type="class", id="uuid-a"),
        CodeEntity(name="B", entity_type="class", id="uuid-b"),
    ]
    relations = [
        ExtractedRelation(
            source_name="A",
            source_type="class",
            target_name="B",
            target_type="class",
            relation_type="contains",
            file_path="test.py",
        ),
        ExtractedRelation(
            source_name="A",
            source_type="class",
            target_name="NonExistent",
            target_type="class",
            relation_type="contains",
            file_path="test.py",
        ),
        ExtractedRelation(
            source_name="Missing",
            source_type="class",
            target_name="B",
            target_type="class",
            relation_type="contains",
            file_path="test.py",
        ),
    ]

    extractor = RelationExtractor()
    extractor.add_parse_result(entities, relations)

    # Act
    resolved = extractor.resolve(entities)

    # Assert
    assert len(resolved) == 1
    assert resolved[0].source_id == "uuid-a"
    assert resolved[0].target_id == "uuid-b"


def test_resolve_multiple_files() -> None:
    """测试多文件聚合解析。"""
    # Arrange
    file1_entities = [
        CodeEntity(name="module1", entity_type="module", id="uuid-m1"),
        CodeEntity(name="func1", entity_type="function", id="uuid-f1"),
    ]
    file1_relations = [
        ExtractedRelation(
            source_name="module1",
            source_type="module",
            target_name="func1",
            target_type="function",
            relation_type="contains",
            file_path="file1.py",
        )
    ]

    file2_entities = [
        CodeEntity(name="module2", entity_type="module", id="uuid-m2"),
        CodeEntity(name="func2", entity_type="function", id="uuid-f2"),
    ]
    file2_relations = [
        ExtractedRelation(
            source_name="module2",
            source_type="module",
            target_name="func2",
            target_type="function",
            relation_type="contains",
            file_path="file2.py",
        )
    ]

    all_entities = file1_entities + file2_entities

    extractor = RelationExtractor()
    extractor.add_parse_result(file1_entities, file1_relations)
    extractor.add_parse_result(file2_entities, file2_relations)

    # Act
    resolved = extractor.resolve(all_entities)

    # Assert
    assert len(resolved) == 2
    source_ids = {r.source_id for r in resolved}
    target_ids = {r.target_id for r in resolved}
    assert source_ids == {"uuid-m1", "uuid-m2"}
    assert target_ids == {"uuid-f1", "uuid-f2"}


def test_name_to_id_mapping() -> None:
    """测试名称→ID 映射正确性。"""
    # Arrange
    entities = [
        CodeEntity(name="module", entity_type="module", id="uuid-1"),
        CodeEntity(name="Class", entity_type="class", id="uuid-2"),
        CodeEntity(name="Class.method", entity_type="function", id="uuid-3"),
    ]
    relations = [
        ExtractedRelation(
            source_name="module",
            source_type="module",
            target_name="Class",
            target_type="class",
            relation_type="contains",
            file_path="test.py",
        ),
        ExtractedRelation(
            source_name="Class",
            source_type="class",
            target_name="Class.method",
            target_type="function",
            relation_type="contains",
            file_path="test.py",
        ),
    ]

    extractor = RelationExtractor()
    extractor.add_parse_result(entities, relations)

    # Act
    resolved = extractor.resolve(entities)

    # Assert
    assert len(resolved) == 2
    # 验证 module->Class
    module_class = next(r for r in resolved if r.source_id == "uuid-1")
    assert module_class.target_id == "uuid-2"
    # 验证 Class->method
    class_method = next(r for r in resolved if r.source_id == "uuid-2")
    assert class_method.target_id == "uuid-3"


def test_full_pipeline() -> None:
    """测试完整解析+提取+resolve 流程。"""
    # Arrange
    entities = [
        CodeEntity(name="models", entity_type="module", id="uuid-m"),
        CodeEntity(name="User", entity_type="class", id="uuid-u"),
        CodeEntity(name="User.save", entity_type="function", id="uuid-s"),
        CodeEntity(name="BaseModel", entity_type="class", id="uuid-bm"),
    ]
    relations = [
        ExtractedRelation(
            source_name="models",
            source_type="module",
            target_name="User",
            target_type="class",
            relation_type="contains",
            file_path="models.py",
        ),
        ExtractedRelation(
            source_name="User",
            source_type="class",
            target_name="User.save",
            target_type="function",
            relation_type="contains",
            file_path="models.py",
        ),
        ExtractedRelation(
            source_name="User",
            source_type="class",
            target_name="BaseModel",
            target_type="class",
            relation_type="extends",
            file_path="models.py",
        ),
    ]

    extractor = RelationExtractor()
    extractor.add_parse_result(entities, relations)

    # Act
    resolved = extractor.resolve(entities)

    # Assert
    assert len(resolved) == 3
    relation_types = {r.relation_type for r in resolved}
    assert relation_types == {"contains", "extends"}

    # 验证 contains 关系
    contains_rels = [r for r in resolved if r.relation_type == "contains"]
    assert len(contains_rels) == 2

    # 验证 extends 关系
    extends_rels = [r for r in resolved if r.relation_type == "extends"]
    assert len(extends_rels) == 1
    assert extends_rels[0].source_id == "uuid-u"
    assert extends_rels[0].target_id == "uuid-bm"


def test_resolve_imports_relations() -> None:
    """测试 imports 关系也参与 resolve。"""
    # Arrange
    entities = [
        CodeEntity(name="mymodule", entity_type="module", id="uuid-1"),
        CodeEntity(name="os", entity_type="module", id="uuid-2"),
        CodeEntity(name="json", entity_type="module", id="uuid-3"),
    ]
    relations = [
        ExtractedRelation(
            source_name="mymodule",
            source_type="module",
            target_name="os",
            target_type="module",
            relation_type="imports",
            file_path="test.py",
        ),
        ExtractedRelation(
            source_name="mymodule",
            source_type="module",
            target_name="json",
            target_type="module",
            relation_type="imports",
            file_path="test.py",
        ),
    ]

    extractor = RelationExtractor()
    extractor.add_parse_result(entities, relations)

    # Act
    resolved = extractor.resolve(entities)

    # Assert
    assert len(resolved) == 2
    assert all(r.relation_type == "imports" for r in resolved)
    target_ids = {r.target_id for r in resolved}
    assert target_ids == {"uuid-2", "uuid-3"}

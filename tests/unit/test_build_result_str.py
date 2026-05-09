from __future__ import annotations

from layerkg.builder import BuildResult


class TestBuildResultStr:
    """测试 BuildResult.__str__() 方法。"""

    def test_build_result_str_contains_all_fields(self) -> None:
        # Arrange & Act
        result = BuildResult(
            files_scanned=10,
            entities_created=100,
            relations_created=50,
            doc_entities_created=3,
            concepts_created=5,
            semantic_relations_created=7,
            modules_created=2,
        )
        output = str(result)

        # Assert - 验证包含所有必要字段
        assert "Build Report:" in output
        assert "Files scanned:" in output
        assert "10" in output
        assert "Entities created:" in output
        assert "100" in output
        assert "Relations created:" in output
        assert "50" in output
        assert "Doc entities:" in output
        assert "3" in output
        assert "Concepts:" in output
        assert "5" in output
        assert "Semantic rels:" in output
        assert "7" in output
        assert "Modules:" in output
        assert "2" in output
        assert "Semantic stage:" in output
        assert "[+] completed" in output
        assert "Build status:" in output
        assert "[+] success" in output
        assert "Elapsed:" in output

    def test_build_result_str_errors(self) -> None:
        # Arrange & Act
        result = BuildResult(
            files_scanned=1,
            entities_created=0,
            relations_created=0,
            errors=["error 1", "error 2"],
        )
        output = str(result)

        # Assert
        assert "Errors (2):" in output
        assert "- error 1" in output
        assert "- error 2" in output

    def test_build_result_str_aborted(self) -> None:
        # Arrange & Act
        result = BuildResult(
            files_scanned=0,
            entities_created=0,
            relations_created=0,
            aborted=True,
        )
        output = str(result)

        # Assert
        assert "[X] aborted" in output

    def test_build_result_str_skipped_semantic(self) -> None:
        # Arrange & Act
        result = BuildResult(
            files_scanned=5,
            entities_created=10,
            relations_created=5,
            skipped_semantic=True,
        )
        output = str(result)

        # Assert
        assert "[!] skipped" in output

    def test_build_result_str_elapsed_ms(self) -> None:
        # Arrange & Act
        result = BuildResult(
            files_scanned=1,
            entities_created=1,
            relations_created=0,
            elapsed_ms=1234.56,
        )
        output = str(result)

        # Assert
        assert "1235ms" in output  # 四舍五入

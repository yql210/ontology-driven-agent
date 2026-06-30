from __future__ import annotations

import pytest

from ontoagent.domain.shapes import PathExpression
from ontoagent.execution.path_compiler import PathCompiler


@pytest.fixture
def compiler() -> PathCompiler:
    return PathCompiler()


class TestSelfPath:
    def test_self_returns_empty(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("SELF")
        cypher, params = compiler.compile(expr)
        assert cypher == ""
        assert params == {}


class TestSingleHop:
    def test_single_hop_forward(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("PROCESSES_DATA -> DataAsset")
        cypher, params = compiler.compile(expr)
        assert cypher == "MATCH (n)-[:PROCESSES_DATA]->(collected:DataAsset)"
        assert params == {}

    def test_single_hop_custom_source_var(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("PROCESSES_DATA -> DataAsset")
        cypher, _ = compiler.compile(expr, source_var="source")
        assert cypher == "MATCH (source)-[:PROCESSES_DATA]->(collected:DataAsset)"


class TestReverse:
    def test_reverse_single_hop(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("^CALLS -> CodeEntity")
        cypher, _ = compiler.compile(expr)
        assert cypher == "MATCH (n)<-[:CALLS]-(collected:CodeEntity)"

    def test_reverse_with_quantifier(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("^CALLS+ -> CodeEntity", max_depth=3)
        cypher, _ = compiler.compile(expr)
        assert cypher == "MATCH (n)<-[:CALLS*1..3]-(collected:CodeEntity)"


class TestVariableLength:
    def test_plus_quantifier(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("CALLS+ -> CodeEntity", max_depth=3)
        cypher, _ = compiler.compile(expr)
        assert cypher == "MATCH (n)-[:CALLS*1..3]->(collected:CodeEntity)"

    def test_star_quantifier(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("CALLS* -> CodeEntity", max_depth=3)
        cypher, _ = compiler.compile(expr)
        assert cypher == "MATCH (n)-[:CALLS*0..3]->(collected:CodeEntity)"

    def test_exact_quantifier(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("CALLS{2} -> CodeEntity", max_depth=3)
        cypher, _ = compiler.compile(expr)
        assert cypher == "MATCH (n)-[:CALLS*2..2]->(collected:CodeEntity)"

    def test_range_quantifier(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("CALLS{1,3} -> CodeEntity", max_depth=3)
        cypher, _ = compiler.compile(expr)
        assert cypher == "MATCH (n)-[:CALLS*1..3]->(collected:CodeEntity)"

    def test_open_range_capped_at_max_depth(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("CALLS{2,} -> CodeEntity", max_depth=3)
        cypher, _ = compiler.compile(expr)
        assert cypher == "MATCH (n)-[:CALLS*2..3]->(collected:CodeEntity)"

    def test_range_above_max_depth_is_capped(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("CALLS{1,10} -> CodeEntity", max_depth=3)
        cypher, _ = compiler.compile(expr)
        assert cypher == "MATCH (n)-[:CALLS*1..3]->(collected:CodeEntity)"


class TestSequence:
    def test_two_hops(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("CALLS / IMPLEMENTS -> CodeEntity", max_depth=3)
        cypher, _ = compiler.compile(expr)
        assert cypher == "MATCH (n)-[:CALLS]->(b0)-[:IMPLEMENTS]->(collected:CodeEntity)"

    def test_three_hops(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("CALLS / IMPLEMENTS / CONTAINS -> CodeEntity", max_depth=3)
        cypher, _ = compiler.compile(expr)
        assert cypher == ("MATCH (n)-[:CALLS]->(b0)-[:IMPLEMENTS]->(b1)-[:CONTAINS]->(collected:CodeEntity)")

    def test_sequence_with_reverse_first_hop(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("^CALLS / IMPLEMENTS -> CodeEntity", max_depth=3)
        cypher, _ = compiler.compile(expr)
        assert cypher == "MATCH (n)<-[:CALLS]-(b0)-[:IMPLEMENTS]->(collected:CodeEntity)"

    def test_sequence_with_quantifier_in_first_hop(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("CALLS+ / IMPLEMENTS -> CodeEntity", max_depth=3)
        cypher, _ = compiler.compile(expr)
        assert cypher == "MATCH (n)-[:CALLS*1..3]->(b0)-[:IMPLEMENTS]->(collected:CodeEntity)"


class TestSecurityValidation:
    def test_unknown_relation_raises_value_error(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("HACKED_REL -> CodeEntity")
        with pytest.raises(ValueError, match="HACKED_REL"):
            compiler.compile(expr)

    def test_unknown_relation_in_second_hop_raises(self, compiler: PathCompiler) -> None:
        expr = PathExpression.parse("CALLS / MALICIOUS_REL -> CodeEntity", max_depth=3)
        with pytest.raises(ValueError, match="MALICIOUS_REL"):
            compiler.compile(expr)

    def test_validated_against_ontology_whitelist(self, compiler: PathCompiler) -> None:
        """确认合法本体关系都能编译通过。"""
        for rel in ["CALLS", "IMPLEMENTS", "EXTENDS", "IMPORTS", "CONTAINS", "PROCESSES_DATA"]:
            expr = PathExpression.parse(f"{rel} -> CodeEntity")
            cypher, _ = compiler.compile(expr)
            assert f"[:{rel}]" in cypher

from __future__ import annotations

from ontoagent.parsing.parser.base import BaseParser, ExtractedRelation, ParseResult
from ontoagent.parsing.parser.doc_parser import DocParser, DocParseResult
from ontoagent.parsing.parser.java_parser import JavaParser
from ontoagent.parsing.parser.python_parser import PythonParser

__all__ = [
    "BaseParser",
    "PythonParser",
    "JavaParser",
    "DocParser",
    "ExtractedRelation",
    "ParseResult",
    "DocParseResult",
]

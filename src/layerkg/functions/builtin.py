"""Builtin functions registered via the new ActionContext-based signature."""

from __future__ import annotations

from layerkg.actions.code import (
    extract_interface as _legacy_extract,
)
from layerkg.actions.code import (
    generate_api_doc as _legacy_doc,
)
from layerkg.actions.code import (
    split_large_function as _legacy_split,
)
from layerkg.actions.code import (
    trace_call_chain as _legacy_trace,
)
from layerkg.functions.adapter import adapt_legacy_function
from layerkg.functions.registry import get_function, register_function


def register_all() -> None:
    """Register all builtin functions (idempotent)."""
    if get_function("check_refactor_eligibility") is None:
        register_function("check_refactor_eligibility")(
            adapt_legacy_function("check_refactor_eligibility", _legacy_split)
        )
    if get_function("trace_call_chain") is None:
        register_function("trace_call_chain")(adapt_legacy_function("trace_call_chain", _legacy_trace))
    if get_function("generate_api_doc") is None:
        register_function("generate_api_doc")(adapt_legacy_function("generate_api_doc", _legacy_doc))
    if get_function("extract_interface") is None:
        register_function("extract_interface")(adapt_legacy_function("extract_interface", _legacy_extract))


register_all()

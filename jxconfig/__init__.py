"""
JX — JSON eXtended (Minimal Variant)

A small, deterministic JSON-compatible configuration language with
inline comments, trailing commas, and numeric expressions with variables.
"""

from jxconfig.core import (
    load_jx,
    load_module,
    evaluate,
    eval_expression,
    eval_object,
    strip_comments,
    quote_unquoted_keys,
    strip_trailing_commas,
    quote_expressions,
    process_directives,
    get_by_path,
    get_selected_items,
)

__all__ = [
    "load_jx",
    "load_module",
    "evaluate",
    "eval_expression",
    "eval_object",
    "strip_comments",
    "quote_unquoted_keys",
    "strip_trailing_commas",
    "quote_expressions",
    "process_directives",
    "get_by_path",
    "get_selected_items",
]

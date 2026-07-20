"""
JX — JSON eXtended (Minimal Variant)

This module implements a small, deterministic JSON-compatible configuration
language with three deliberate extensions:

1. Inline comments
   - Supports single-line comments starting with `//`
   - Block comments (`/* ... */`) are not allowed

2. Trailing commas
   - Allowed in objects and arrays
   - Removed during preprocessing before JSON parsing

3. Numeric expressions with variables
   - Object keys define variables implicitly
   - Variables may be referenced later in the same object using `${name}`
   - Expressions support `+ - * / ( )`
   - Evaluation is order-dependent and single-pass
   - Nested objects create nested scopes (inner scopes shadow outer ones)
   - Import other JX files using `#include "file"[path] as alias`
     - Optionally specify a path within the included file to extract specific data

This module is intended as a lightweight preprocessor for configuration files,
not as a general-purpose programming language.
"""

import importlib
import json
import math
import os
import re
from types import SimpleNamespace
from typing import Any

COMMENT_RE = re.compile(r"//.*?$", re.MULTILINE)
TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")

# Match unquoted JSON keys: word + optional whitespace + colon
# Handles both line-start indent and comma-separated forms
UNQUOTED_KEY_RE = re.compile(
    r"(^[ \t]*|,\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", re.MULTILINE
)

# Capture #include "file"[path] as alias
INCLUDE_RE = re.compile(
    r'^\s*#include\s+"(?P<file>[^"]+)"(?:\s*\[(?P<path>[^\]]+)\])?(?:\s+as\s+(?P<alias>[A-Za-z_]\w*))?', re.MULTILINE
)

# Capture function calls, bare words, and math
EXPR_VALUE_RE = re.compile(
    r"""
    :                       # colon starting a value
    \s*
    (                       # capture the expression
        (?:
            \$\{[A-Za-z_][A-Za-z0-9_]*\} |  # Variables ${name}
            [A-Za-z_][A-Za-z0-9_]* |  # Functions/Constants
            [\d.]                        |  # Numbers
            [+\-*/(),]                   |  # Operators
            \s                              # Whitespace
        )+
    )
    (?=\s*[,\}])            # must end before , or }
    """,
    re.VERBOSE,
)

INTERP_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Allow letters (for function names) and commas in safety check
ALLOWED_CHARS_RE = re.compile(r"^[A-Za-z0-9+\-*/().,_\s]+$")

MATH_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}

STATIC_GLOBALS = {
    "true": True,
    "false": False,
    "null": None,
    "nan": float("nan"),
    "inf": float("inf"),
    "-inf": float("-inf"),
}


def load_module(module_name: str) -> dict:
    module = importlib.import_module(module_name)
    return {name: getattr(module, name) for name in dir(module) if not name.startswith("_")}


allowed_functions = {**load_module("math"), "max": max, "min": min, "round": round, "abs": abs}


def get_by_path(data: Any, path: str) -> Any:
    """Navigate through a dictionary using a dot-separated string."""
    if not path:
        return data
    for part in path.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        else:
            raise KeyError(f"Path component '{part}' not found")
    return data


def get_selected_items(data: dict, path_str: str) -> dict:
    """Extract multiple specific keys/paths into a new dictionary."""
    selected = {}
    for part in [p.strip() for p in path_str.split(",")]:
        val = get_by_path(data, part)
        key = part.split(".")[-1]  # Use the last part of the path as the key
        selected[key] = val
    return selected


def process_directives(text: str, current_dir: str, seen_files: set) -> tuple[str, dict]:
    """Handle #include directives before JSON parsing."""
    initial_scope = {}

    def replace_include(match):
        gd = match.groupdict()
        filename = gd["file"]
        path_str = gd["path"]
        alias = gd["alias"]

        # Resolve absolute path to prevent redundant loads and circularity
        file_path = os.path.abspath(os.path.join(current_dir, filename))

        if file_path in seen_files:
            raise RecursionError(f"Circular include: {file_path}")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Missing include: {file_path}")

        with open(file_path, encoding="utf-8") as f:
            # Recursively load the included file
            included_data = load_jx(f.read(), os.path.dirname(file_path), seen_files | {file_path})

        # Determine what data to extract
        if path_str:
            if "," in path_str:
                target_data = get_selected_items(included_data, path_str)
            else:
                target_data = get_by_path(included_data, path_str)
        else:
            target_data = included_data

        # Inject into the current file's scope
        if alias:
            initial_scope[alias] = target_data
        elif isinstance(target_data, dict):
            initial_scope.update(target_data)
        else:
            raise ValueError("Include must be an object/dict unless using an 'as' alias.")

        return ""  # Remove the #include line from the text

    processed_text = INCLUDE_RE.sub(replace_include, text)
    return processed_text, initial_scope


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub("", text)


def quote_unquoted_keys(text: str) -> str:
    """Wrap bare identifier keys in double quotes so the result is valid JSON."""
    return UNQUOTED_KEY_RE.sub(r'\1"\2"\3', text)


def strip_trailing_commas(text: str) -> str:
    return TRAILING_COMMA_RE.sub(r"\1", text)


def quote_expressions(text: str) -> str:
    def repl(match):
        expr = match.group(1).strip()

        # If it looks like a number, leave it alone (let json.loads handle it)
        if re.fullmatch(r"[\d.]+", expr):
            return f": {expr}"

        # Add a special prefix marker so evaluate() knows this is code
        return f': "__jx_expr__{expr}"'

    return EXPR_VALUE_RE.sub(repl, text)


# -----------------------------
# Expression Evaluation
# -----------------------------


def eval_expression(expr: str, scope: dict) -> Any:
    # 1. Interpolate variables ${var}
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", expr.strip())
    if match:
        name = match.group(1)
        if name in scope:
            return scope[name]
        raise NameError(f"Undefined variable '{name}'")

    # 2. Replace all ${var} with their values
    expanded = INTERP_RE.sub(r"\1", expr)

    # 2. Security Check
    if not ALLOWED_CHARS_RE.fullmatch(expanded):
        raise ValueError(f"Invalid characters in expression: {expanded}")

    # 4. # 3. Evaluate with math functions and context
    def wrap(v):
        if isinstance(v, dict):
            return SimpleNamespace(**{k: wrap(v[k]) for k in v})
        return v

    # Wrap dictionaries in the scope to allow attribute access
    wrapped_scope = {k: wrap(v) for k, v in scope.items()}

    return eval(expanded, {"__builtins__": allowed_functions, **wrapped_scope}, {})


def evaluate(value: Any, scope: dict) -> Any:
    if isinstance(value, dict):
        return eval_object(value, scope)
    if isinstance(value, list):
        return [evaluate(v, scope) for v in value]

    # Check for the marker OR standard interpolation
    if isinstance(value, str):
        if value.startswith("__jx_expr__"):
            raw_expr = value.replace("__jx_expr__", "")
            return eval_expression(raw_expr, scope)
        if "${" in value:
            return eval_expression(value, scope)

    return value


def eval_object(obj: dict, parent_scope: dict) -> dict:
    scope = dict(parent_scope)
    result = {}

    for key, raw_value in obj.items():
        value = evaluate(raw_value, scope)
        result[key] = value
        scope[key] = value

    return result


def load_jx(text: str, current_dir: str = ".", seen_files: set | None = None) -> Any:
    if seen_files is None:
        seen_files = set()

    # 1. Pre-process #include directives
    text, included_scope = process_directives(text, current_dir, seen_files)

    # 2. Existing cleanup
    text = strip_comments(text)
    text = quote_unquoted_keys(text)
    text = strip_trailing_commas(text)
    text = quote_expressions(text)

    # 3. Parse and Evaluate with the combined scope
    data = json.loads(text)
    full_scope = {**MATH_CONSTANTS, **STATIC_GLOBALS, **included_scope}

    evaluated_data = evaluate(data, full_scope)

    if isinstance(evaluated_data, dict):
        return {**included_scope, **evaluated_data}
    return evaluated_data

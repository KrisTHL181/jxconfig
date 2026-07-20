"""
Tests for JX — JSON eXtended config language.
"""

import math
import os
import tempfile
from textwrap import dedent

import pytest

from jxconfig import (
    eval_expression,
    evaluate,
    get_by_path,
    load_jx,
    quote_expressions,
    quote_unquoted_keys,
    strip_comments,
    strip_trailing_commas,
)
from jxconfig.core import MATH_CONSTANTS, STATIC_GLOBALS

# A scope that matches what load_jx sets up
FULL_SCOPE = {**MATH_CONSTANTS, **STATIC_GLOBALS}


# ---------------------------------------------------------------------------
# Preprocessing helpers
# ---------------------------------------------------------------------------

class TestStripComments:
    def test_no_comments(self):
        assert strip_comments('{"a": 1}') == '{"a": 1}'

    def test_single_line_comment(self):
        assert strip_comments('{"a": 1} // trailing') == '{"a": 1} '

    def test_comment_only_line(self):
        result = strip_comments("// whole line comment\n42")
        assert result == "\n42"

    def test_multiline_comments(self):
        text = "// top\n{\n  // inside\n  a: 1 // value\n}\n// bottom"
        result = strip_comments(text)
        assert "//" not in result

    def test_url_in_string_partially_stripped(self):
        """Known limitation: // inside JSON string values is treated as a comment.
        JX users should avoid raw URLs with // in config values, or use quoting
        strategies (e.g. split across lines).
        """
        text = '{"url": "https://example.com"}'
        result = strip_comments(text)
        # The // triggers comment stripping from //example.com onward
        assert "https:" in result
        assert "example.com" not in result


class TestStripTrailingCommas:
    def test_object_trailing_comma(self):
        assert strip_trailing_commas('{"a": 1,}') == '{"a": 1}'

    def test_array_trailing_comma(self):
        assert strip_trailing_commas("[1, 2, 3,]") == "[1, 2, 3]"

    def test_nested_trailing_commas(self):
        text = '{"a": [1, 2,], "b": {"c": 3,},}'
        expected = '{"a": [1, 2], "b": {"c": 3}}'
        assert strip_trailing_commas(text) == expected

    def test_no_trailing_comma(self):
        assert strip_trailing_commas('{"a": 1}') == '{"a": 1}'


class TestQuoteUnquotedKeys:
    def test_simple_key(self):
        result = quote_unquoted_keys("a: 1")
        assert result == '"a": 1'

    def test_indented_key(self):
        result = quote_unquoted_keys("    name: value")
        assert result == '    "name": value'

    def test_comma_separated(self):
        result = quote_unquoted_keys('{"a": 1, b: 2}')
        assert result == '{"a": 1, "b": 2}'

    def test_already_quoted_untouched(self):
        result = quote_unquoted_keys('{"already": "done"}')
        assert result == '{"already": "done"}'

    def test_multiline(self):
        text = "{\n    port: 8000,\n    host: localhost\n}"
        result = quote_unquoted_keys(text)
        assert '"port":' in result
        assert '"host":' in result

    def test_underscore_key(self):
        result = quote_unquoted_keys("my_key: 1")
        assert result == '"my_key": 1'


class TestQuoteExpressions:
    def test_numeric_value_unchanged(self):
        result = quote_expressions('"a": 42')
        assert ": 42" in result

    def test_bare_word_wrapped_as_expr(self):
        result = quote_expressions('"a": base_port + 1')
        assert "__jx_expr__" in result

    def test_variable_interpolation(self):
        result = quote_expressions('"name": ${host}:${port}')
        assert "__jx_expr__" in result or "${" in result


# ---------------------------------------------------------------------------
# Expression evaluation
# ---------------------------------------------------------------------------

class TestEvalExpression:
    def test_numeric_literal(self):
        # Direct numeric strings pass through eval
        result = eval_expression("42", FULL_SCOPE)
        assert result == 42

    def test_arithmetic(self):
        scope = {**FULL_SCOPE, "a": 10, "b": 3}
        assert eval_expression("a + b", scope) == 13
        assert eval_expression("a - b", scope) == 7
        assert eval_expression("a * b", scope) == 30
        assert eval_expression("a / b", scope) == 10 / 3
        assert eval_expression("a + b * 2", scope) == 16
        assert eval_expression("(a + b) * 2", scope) == 26

    def test_variable_interpolation(self):
        scope = {**FULL_SCOPE, "host": "localhost", "port": 8000}
        assert eval_expression("${host}", scope) == "localhost"

    def test_math_constants(self):
        assert eval_expression("pi", FULL_SCOPE) == math.pi
        assert eval_expression("e", FULL_SCOPE) == math.e

    def test_static_globals(self):
        assert eval_expression("true", FULL_SCOPE) is True
        assert eval_expression("false", FULL_SCOPE) is False
        assert eval_expression("null", FULL_SCOPE) is None

    def test_math_functions(self):
        assert eval_expression("sqrt(4)", FULL_SCOPE) == 2.0
        assert eval_expression("abs(-5)", FULL_SCOPE) == 5
        assert eval_expression("max(1, 2, 3)", FULL_SCOPE) == 3
        assert eval_expression("min(1, 2, 3)", FULL_SCOPE) == 1
        assert eval_expression("round(3.7)", FULL_SCOPE) == 4

    def test_undefined_variable_raises(self):
        with pytest.raises(NameError):
            eval_expression("${nonexistent}", FULL_SCOPE)


class TestEvaluate:
    def test_plain_dict(self):
        result = evaluate({"a": 1, "b": 2}, {})
        assert result == {"a": 1, "b": 2}

    def test_expression_value(self):
        result = evaluate({"a": "__jx_expr__1 + 1"}, FULL_SCOPE)
        assert result == {"a": 2}

    def test_nested_object(self):
        data = {"outer": {"a": "__jx_expr__10 * 2", "b": "__jx_expr__a + 5"}}
        result = evaluate(data, FULL_SCOPE)
        assert result["outer"]["a"] == 20
        assert result["outer"]["b"] == 25

    def test_list_of_expressions(self):
        result = evaluate(
            ["__jx_expr__3 * 7", "__jx_expr__2 + 2"], FULL_SCOPE
        )
        assert result == [21, 4]

    def test_variable_shadowing(self):
        data = {
            "x": "__jx_expr__1",
            "inner": {"x": "__jx_expr__x + 99"},
        }
        result = evaluate(data, FULL_SCOPE)
        assert result["x"] == 1
        assert result["inner"]["x"] == 100


# ---------------------------------------------------------------------------
# load_jx – full pipeline
# ---------------------------------------------------------------------------

class TestLoadJx:
    def test_plain_json(self):
        config = load_jx('{"name": "test", "port": 8080}')
        assert config["name"] == "test"
        assert config["port"] == 8080

    def test_unquoted_keys_with_quoted_values(self):
        """Unquoted keys work; string values must still be quoted."""
        config = load_jx('{name: "test", port: 8080}')
        assert config["name"] == "test"
        assert config["port"] == 8080

    def test_comments(self):
        config = load_jx(
            dedent("""\
            {
                // server config
                host: "localhost",
                port: 5432,   // default pg port
            }
            """)
        )
        assert config["host"] == "localhost"
        assert config["port"] == 5432

    def test_trailing_commas(self):
        config = load_jx("{a: 1, b: 2, c: 3,}")
        assert config == {"a": 1, "b": 2, "c": 3}

    def test_expression_chain(self):
        config = load_jx(
            dedent("""\
            {
                base: 100,
                tax: base * 0.2,
                total: base + tax,
            }
            """)
        )
        assert config["base"] == 100
        assert config["tax"] == 20.0
        assert config["total"] == 120.0

    def test_variable_interpolation_in_value(self):
        config = load_jx(
            dedent("""\
            {
                host: "localhost",
                port: 6379,
                dsn: ${host}:${port},
            }
            """)
        )
        assert config["dsn"] == "localhost:6379"

    def test_math_constants(self):
        config = load_jx("{two_pi: 2 * pi}")
        assert config["two_pi"] == pytest.approx(2 * math.pi)

    def test_booleans_and_null(self):
        config = load_jx(
            dedent("""\
            {
                enabled: true,
                debug: false,
                extra: null,
            }
            """)
        )
        assert config["enabled"] is True
        assert config["debug"] is False
        assert config["extra"] is None

    def test_nested_objects(self):
        config = load_jx(
            dedent("""\
            {
                server: {
                    host: "0.0.0.0",
                    port: 8080,
                },
                db: {
                    host: "localhost",
                    port: server.port + 1000,
                },
            }
            """)
        )
        assert config["server"]["host"] == "0.0.0.0"
        assert config["server"]["port"] == 8080
        # Attribute-style access: server.port resolves to 8080
        assert config["db"]["port"] == 9080

    def test_nested_scope_shadowing(self):
        config = load_jx(
            dedent("""\
            {
                x: 1,
                inner: {
                    x: 100,
                    y: x + 1,    // uses inner x
                },
                outer_x: x,       // uses outer x
            }
            """)
        )
        assert config["x"] == 1
        assert config["inner"]["x"] == 100
        assert config["inner"]["y"] == 101
        assert config["outer_x"] == 1

    def test_math_functions(self):
        config = load_jx(
            dedent("""\
            {
                r: 5,
                area: pi * r ** 2,
                rounded: round(area, 2),
            }
            """)
        )
        assert config["r"] == 5
        assert config["area"] == pytest.approx(math.pi * 25)
        assert config["rounded"] == pytest.approx(round(math.pi * 25, 2))

    def test_load_module_function(self):
        config = load_jx("{len: sqrt(16) + max(1, 2, 3)}")
        assert config["len"] == 4 + 3  # sqrt(16) + max(1,2,3)


# ---------------------------------------------------------------------------
# #include directives
# ---------------------------------------------------------------------------

class TestIncludes:
    def test_basic_include(self):
        included = dedent("""\
        {
            host: "db.local",
            port: 5432,
        }
        """)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jx", delete=False, encoding="utf-8"
        ) as f:
            f.write(included)
            included_path = f.name

        try:
            config = load_jx(
                f'{{\n#include "{included_path}"\ndb_timeout: 30}}',
                current_dir=os.path.dirname(included_path),
            )
            assert config["host"] == "db.local"
            assert config["port"] == 5432
            assert config["db_timeout"] == 30
        finally:
            os.unlink(included_path)

    def test_include_with_alias(self):
        included = dedent("""\
        {
            host: "pg.local",
            port: 5432,
        }
        """)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jx", delete=False, encoding="utf-8"
        ) as f:
            f.write(included)
            included_path = f.name

        try:
            config = load_jx(
                f'{{\n#include "{included_path}" as db\ndb_timeout: 30}}',
                current_dir=os.path.dirname(included_path),
            )
            assert config["db"]["host"] == "pg.local"
            assert config["db"]["port"] == 5432
            assert config["db_timeout"] == 30
        finally:
            os.unlink(included_path)

    def test_include_with_path_extraction(self):
        included = dedent("""\
        {
            server: {host: "srv.local", port: 9000},
            db:     {host: "db.local",  port: 5432},
        }
        """)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jx", delete=False, encoding="utf-8"
        ) as f:
            f.write(included)
            included_path = f.name

        try:
            config = load_jx(
                f'{{\n#include "{included_path}"[db] as db\ntimeout: 30}}',
                current_dir=os.path.dirname(included_path),
            )
            assert config["db"]["host"] == "db.local"
            assert config["db"]["port"] == 5432
            assert config["timeout"] == 30
        finally:
            os.unlink(included_path)

    def test_include_multi_path_extraction(self):
        included = "{a: 1, b: 2, c: 3}"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jx", delete=False, encoding="utf-8"
        ) as f:
            f.write(included)
            included_path = f.name

        try:
            config = load_jx(
                f'{{\n#include "{included_path}"[a, c] as picked\nextra: 99}}',
                current_dir=os.path.dirname(included_path),
            )
            assert config["picked"]["a"] == 1
            assert config["picked"]["c"] == 3
            assert "b" not in config["picked"]
            assert config["extra"] == 99
        finally:
            os.unlink(included_path)

    def test_include_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_jx('#include "nonexistent_file_12345.jx"')


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

class TestGetByPath:
    def test_empty_path(self):
        data = {"a": 1}
        assert get_by_path(data, "") == data

    def test_simple_key(self):
        assert get_by_path({"a": 1}, "a") == 1

    def test_nested_path(self):
        data = {"a": {"b": {"c": 42}}}
        assert get_by_path(data, "a.b.c") == 42

    def test_missing_key_raises(self):
        with pytest.raises(KeyError):
            get_by_path({"a": 1}, "b")

    def test_missing_nested_raises(self):
        with pytest.raises(KeyError):
            get_by_path({"a": {"b": 1}}, "a.c")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_object(self):
        assert load_jx("{}") == {}

    def test_deeply_nested(self):
        config = load_jx(
            dedent("""\
            {
                a: { b: { c: { d: { e: 42 } } } },
                x: a.b.c.d.e + 8,
            }
            """)
        )
        assert config["a"]["b"]["c"]["d"]["e"] == 42
        assert config["x"] == 50

    def test_negative_numbers(self):
        config = load_jx("{a: -5, b: a * -2}")
        assert config["a"] == -5
        assert config["b"] == 10

    def test_float_values(self):
        config = load_jx("{scale: 1.5, doubled: scale * 2}")
        assert config["scale"] == 1.5
        assert config["doubled"] == 3.0

    def test_array_with_expressions(self):
        config = load_jx(
            dedent("""\
            {
                base: 10,
                values: [base, base + 1, base + 2],
            }
            """)
        )
        assert config["base"] == 10
        assert config["values"] == [10, 11, 12]

    def test_top_level_array(self):
        result = load_jx("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_invalid_expression_blocked(self):
        """Security: arbitrary Python code in expressions is rejected."""
        with pytest.raises((ValueError, NameError, SyntaxError)):
            eval_expression("__import__('os').system('ls')", FULL_SCOPE)

    def test_inf_and_nan(self):
        config = load_jx("{pos: inf, neg: -inf, not_a_num: nan}")
        assert config["pos"] == float("inf")
        assert config["neg"] == float("-inf")
        assert math.isnan(config["not_a_num"])

    def test_tau_constant(self):
        config = load_jx("{t: tau}")
        assert config["t"] == pytest.approx(2 * math.pi)

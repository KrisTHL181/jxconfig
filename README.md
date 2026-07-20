# JX — JSON eXtended

A minimal, deterministic JSON-compatible configuration language with three
deliberate extensions.  JX is a lightweight preprocessor for configuration
files — not a general-purpose programming language.

## Features

| Extension | Description |
|---|---|
| **Inline comments** | `//` single-line comments (block comments intentionally excluded) |
| **Trailing commas** | Allowed in objects and arrays; stripped before JSON parsing |
| **Numeric expressions** | Object keys define variables; reference them with `${name}`; supports `+ - * / ( )` |

### Beyond the basics

- **`#include` directives** — import other JX files with optional path extraction and
  aliasing: `#include "other.jx"[a.b.c] as alias`
- **Math constants** — `pi`, `e`, `tau` built in
- **Static globals** — `true`, `false`, `null`, `nan`, `inf`, `-inf`
- **All `math` module functions** — plus `max`, `min`, `round`, `abs`
- **Nested scopes** — inner objects shadow outer keys; evaluation is single-pass
  and order-dependent

## Installation

```bash
pip install jxconfig
```

Or from source:

```bash
pip install git+https://github.com/KrisTHL181/jxconfig.git
```

## Quick Start

```python
from jxconfig import load_jx

# Load from a string
config = load_jx('''
{
    // server configuration
    base_port: 8000,
    workers: 4,
    total_ports: base_port + workers,  // 8004
    pi_area: 3 * pi * 2 ** 2,          // math constants
}
''')

print(config["total_ports"])  # 8004
```

### Including other files

**`db.jx`**:
```jsonc
{
    host: "localhost",
    port: 5432,
}
```

**`app.jx`**:
```jsonc
{
    #include "db.jx"[host, port] as db

    name: "myapp",
    connection: "${db.host}:${db.port}",
}
```

### From a file

```python
from jxconfig import load_jx

with open("config.jx") as f:
    config = load_jx(f.read(), current_dir="configs/")

# All top-level keys are accessible by name
print(config["name"])
```

## Language Spec

JX files are valid JSON with three relaxations:

1. **Comments** — `//` to end of line
2. **Trailing commas** — `{"a": 1,}` and `[1, 2,]` are legal
3. **Unquoted expression values** — after `:`, an expression may be written
   without quotes; variables are interpolated with `${name}`

Expressions support the standard arithmetic operators (`+`, `-`, `*`, `/`,
parentheses) plus all Python `math` module functions and constants.

## API

### `load_jx(text: str, current_dir: str = ".") -> dict`

Parse and evaluate a JX string.  `current_dir` sets the base directory for
resolving relative `#include` paths.

### `evaluate(value, scope: dict) -> Any`

Recursively evaluate expressions in a parsed JSON structure.

### `eval_expression(expr: str, scope: dict) -> Any`

Evaluate a single expression string against a variable scope.

### `process_directives(text: str, current_dir: str, seen_files: set) -> tuple[str, dict]`

Pre-process `#include` directives before JSON parsing.

## License

MIT

"""Shaping for the JSON Schemas sent to Ollama as `format`.

A schema-constrained grammar puts no length bound on a JSON string, so a model
that falls into a token-repetition loop inside one can never emit the closing
quote: nothing ends the response but an exhausted context. Measured on
gemma4:26b, one designer plan became 58,930 characters of
"legibilityibilityility...", and because the planner answers `done_reason=length`
by raising num_ctx, the next attempt only had further to run -- a single bad
sample cost half an hour. Bounding every string ends the loop at a length no
real value reaches.
"""

import copy

# The 73 DesignSpecs this pipeline has produced hold 6242 strings: p99 is 279
# characters, p999 is 523, the longest is 609. A bound at 1000 cannot truncate a
# value the models actually write; it only cuts a loop short.
MAX_STRING_LENGTH = 1000


def bound_strings(schema: dict, max_length: int = MAX_STRING_LENGTH) -> dict:
    """Return `schema` with every unconstrained string given a maxLength."""
    bounded = copy.deepcopy(schema)
    _walk(bounded, max_length)
    return bounded


def _walk(node: object, max_length: int) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, max_length)
        return
    if not isinstance(node, dict):
        return
    # An enum already bounds itself, and a stated maxLength is the author's.
    if node.get("type") == "string" and "enum" not in node and "maxLength" not in node:
        node["maxLength"] = max_length
    for value in node.values():
        _walk(value, max_length)


def format_label(format: str | dict | None) -> str | None:
    """A short name for a `format` argument, for log lines.

    A JSON Schema logged in full is thousands of characters that repeat on every
    request and say nothing a reader of the log needs; its title identifies the
    contract just as well.
    """
    if format is None or isinstance(format, str):
        return format
    title = format.get("title")
    return str(title) if title else "schema"

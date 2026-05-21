"""
Tool input validation and repair for LiberCode.

Validates LLM tool call arguments against their JSON schema definitions,
fixes common type mismatches, and strips unknown fields.
"""
from typing import Dict, List, Optional, Tuple


_TYPE_MAP = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "array": list,
}


def try_coerce(value, expected_type: str):
    """Attempt to coerce a value to the expected JSON schema type.

    Returns (coerced_value, True) on success, or (None, False) on failure.
    """
    if expected_type is None:
        return value, True

    target = _TYPE_MAP.get(expected_type)
    if target is None:
        return value, True

    if target is int and isinstance(value, bool):
        return None, False

    if isinstance(value, target):
        return value, True

    try:
        if target is bool:
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes"), True
            return bool(value), True
        if target is int:
            return int(value), True
        if target is str:
            return str(value), True
    except (ValueError, TypeError):
        return None, False

    return None, False


def validate_and_fix_args(
    tool_name: str,
    args: Dict,
    tools_schema_list: List[Dict],
) -> Tuple[Dict, Optional[str], Optional[str]]:
    """Validate and fix tool call arguments against schema.

    Returns (fixed_args, error_message, warning_message).
    - fixed_args: args dict with type coercions applied and unknown fields removed.
    - error_message: None if valid, otherwise a human-readable error string
      that blocks execution and is sent back to the LLM for self-correction.
    - warning_message: None if no warnings, otherwise non-blocking info
      (e.g. unknown fields were stripped). Appended to tool result by caller.
    """
    tool_schema = next(
        (t for t in tools_schema_list if t["name"] == tool_name), None
    )
    if not tool_schema:
        return args, None, None

    schema = tool_schema.get("input_schema", {})
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    errors: List[str] = []
    warnings: List[str] = []
    fixed_args: Dict = {}

    for field in sorted(required):
        if field not in args:
            expected_type = properties.get(field, {}).get("type", "unknown")
            errors.append(
                f"Missing required field '{field}' (expected type: {expected_type})"
            )

    for key, value in args.items():
        if key not in properties:
            warnings.append(f"Unknown field '{key}' is not in schema, ignored")
            continue

        prop_spec = properties[key]
        expected_type = prop_spec.get("type")

        coerced, ok = try_coerce(value, expected_type)
        if not ok:
            actual_type = type(value).__name__
            errors.append(
                f"Field '{key}': expected {expected_type}, "
                f"got {actual_type} '{value}'"
            )
            continue

        if "enum" in prop_spec and coerced not in prop_spec["enum"]:
            errors.append(
                f"Field '{key}': got '{coerced}', "
                f"expected one of {prop_spec['enum']}"
            )
            continue

        fixed_args[key] = coerced

    error_msg = None
    warning_msg = None

    if errors:
        error_msg = (
            f"Tool '{tool_name}' input validation failed:\n"
            + "\n".join(f" - {e}" for e in errors)
        )
        if warnings:
            error_msg += "\n" + "\n".join(f" - {w}" for w in warnings)
        error_msg += "\nPlease retry with the correct fields."
    elif warnings:
        warning_msg = "Tool '{}' warnings:\n".format(tool_name) + "\n".join(
            f" - {w}" for w in warnings
        )

    return fixed_args, error_msg, warning_msg

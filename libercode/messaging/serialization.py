"""
Message serialization utilities for LiberCode.

Handles conversion of complex objects to JSON-serializable formats.
"""

import json
from typing import Any


def serialize_content(content: Any) -> Any:
    """
    Serialize content to JSON-compatible format.

    Handles:
    - Anthropic SDK objects (via model_dump or to_dict)
    - tool_result content (parses JSON strings)
    - Nested lists and dicts
    - Plain values (preserved as-is)

    Args:
        content: Content to serialize

    Returns:
        JSON-serializable content
    """
    # Handle Anthropic SDK objects
    if hasattr(content, "model_dump"):
        try:
            return content.model_dump()
        except (AttributeError, TypeError):
            pass  # Fall through to try to_dict

    if hasattr(content, "to_dict"):
        return content.to_dict()

    # Handle dicts
    if isinstance(content, dict):
        result = {}
        for key, value in content.items():
            result[key] = serialize_content(value)

        # Special handling for tool_result content
        if content.get("type") == "tool_result" and "content" in result:
            # Try to parse JSON string
            if isinstance(result["content"], str):
                try:
                    result["content"] = json.loads(result["content"])
                except json.JSONDecodeError:
                    pass  # Preserve as-is

        return result

    # Handle lists
    if isinstance(content, list):
        return [serialize_content(item) for item in content]

    # Return plain values as-is
    return content

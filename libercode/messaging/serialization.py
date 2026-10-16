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
        return {key: serialize_content(value) for key, value in content.items()}

    # Handle lists
    if isinstance(content, list):
        return [serialize_content(item) for item in content]

    # Return plain values as-is
    return content


def normalize_messages(messages: Any) -> Any:
    """Normalize message history loaded from disk for LLM API compatibility.

    Older versions parsed JSON-string ``tool_result`` content into dicts
    (or other JSON values) when saving sessions. The LLM API requires
    ``tool_result`` content to be a string or a list of content blocks, so
    such values are converted back to a JSON string here.

    Args:
        messages: Message history (list of message dicts)

    Returns:
        The same messages, normalized in place
    """
    if not isinstance(messages, list):
        return messages
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                block_content = block.get("content")
                if block_content is not None and not isinstance(
                    block_content, (str, list)
                ):
                    block["content"] = json.dumps(block_content, ensure_ascii=False)
    return messages

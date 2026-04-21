"""
Message protocol for LiberCode.

Defines message types and message format for inter-agent communication.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Dict, Any
from enum import Enum


class MessageType(str, Enum):
    """Message type enumeration"""
    MESSAGE = "message"
    NOTIFICATION = "notification"
    BROADCAST = "broadcast"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"
    PLAN_APPROVAL = "plan_approval"
    SHUTDOWN_BY_SELF = "shutdown_by_self"


@dataclass
class Message:
    """
    Message data model for inter-agent communication.

    Attributes:
        type: Message type (message, broadcast, etc.)
        sender: Name of sending agent
        content: Message content
        timestamp: Unix timestamp (auto-generated if not provided)
        extra: Additional fields (e.g., request_id, approve flags)
    """
    type: MessageType
    sender: str
    content: str
    timestamp: float = field(default_factory=time.time)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize message to dictionary.

        Returns:
            Dictionary with all message fields
        """
        data = {
            "type": self.type.value,
            "from": self.sender,
            "content": self.content,
            "timestamp": self.timestamp,
        }

        # Merge extra fields
        data.update(self.extra)

        return data

    def to_json(self) -> str:
        """
        Serialize message to JSON string.

        Returns:
            JSON string with all message fields
        """
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "Message":
        """
        Deserialize message from JSON string.

        Args:
            json_str: JSON string with message fields

        Returns:
            Message instance
        """
        data = json.loads(json_str)

        return cls(
            type=MessageType(data["type"]),
            sender=data["from"],
            content=data["content"],
            timestamp=data["timestamp"],
            extra={
                k: v for k, v in data.items()
                if k not in {"type", "from", "content", "timestamp"}
            },
        )

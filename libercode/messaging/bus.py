"""
Message bus for LiberCode.

Provides JSONL-based message passing between agents.
"""

import json
from pathlib import Path
from typing import List

from libercode.messaging.protocol import Message


class MessageBus:
    """
    Message bus for inter-agent communication.

    Each agent has an inbox file (JSONL format) where messages are appended.
    """

    def __init__(self, inbox_dir: Path):
        """
        Initialize message bus.

        Args:
            inbox_dir: Directory to store inbox files
        """
        self.inbox_dir = inbox_dir
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

    def send(self, message: Message, to: str) -> str:
        """
        Send message to recipient.

        Args:
            message: Message to send
            to: Recipient name

        Returns:
            Status message
        """
        inbox_path = self.inbox_dir / f"{to}.jsonl"
        with open(inbox_path, "a") as f:
            f.write(message.to_json() + "\n")
        return f"Sent {message.type.value} to {to}"

    def read_inbox(self, name: str) -> List[Message]:
        """
        Read and drain inbox for named agent.

        Args:
            name: Agent name

        Returns:
            List of messages (inbox is drained after reading)
        """
        inbox_path = self.inbox_dir / f"{name}.jsonl"

        if not inbox_path.exists():
            return []

        messages = []
        for line in inbox_path.read_text().strip().splitlines():
            if line:
                messages.append(Message.from_json(line))

        # Drain inbox
        inbox_path.write_text("")
        return messages

    def broadcast(
        self, sender: str, content: str, teammates: List[str]
    ) -> str:
        """
        Broadcast message to all teammates except sender.

        Args:
            sender: Sender name
            content: Message content
            teammates: List of teammate names

        Returns:
            Status message with count
        """
        from libercode.messaging.protocol import MessageType

        count = 0
        for name in teammates:
            if name != sender:
                msg = Message(
                    type=MessageType.BROADCAST, sender=sender, content=content
                )
                self.send(msg, to=name)
                count += 1

        return f"Broadcast to {count} teammates"

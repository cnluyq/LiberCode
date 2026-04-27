"""
Task data models for LiberCode.
Defines Task and TaskStatus using Python dataclasses.
"""

from dataclasses import dataclass, field
from typing import List
from enum import Enum


class TaskStatus(str, Enum):
    """Task status enumeration"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class Task:
    """
    Task data model.
    Represents a task in the task board with dependencies and ownership.
    """
    id: int
    subject: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    blocked_by: List[int] = field(default_factory=list)
    blocks: List[int] = field(default_factory=list)
    owner: str = ""
    worktree: str = ""
    required_role: str = ""
    assigned_to: str = ""

    def to_dict(self) -> dict:
        """
        Convert to JSON-serializable dict.
        Uses legacy field naming (blockedBy) for backward compatibility.

        Returns:
            Dict with all task fields
        """
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status.value,
            "blockedBy": self.blocked_by,
            "blocks": self.blocks,
            "owner": self.owner,
            "worktree": self.worktree,
            "required_role": self.required_role,
            "assigned_to": self.assigned_to,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """
        Create Task from JSON dict.
        Handles both legacy (blockedBy) and new (blocked_by) field names.

        Args:
            data: Dict with task fields

        Returns:
            Task instance
        """
        return cls(
            id=data["id"],
            subject=data["subject"],
            description=data.get("description", ""),
            status=TaskStatus(data.get("status", "pending")),
            blocked_by=data.get("blockedBy", data.get("blocked_by", [])),
            blocks=data.get("blocks", []),
            owner=data.get("owner", ""),
            worktree=data.get("worktree", ""),
            required_role=data.get("required_role", ""),
            assigned_to=data.get("assigned_to", ""),
        )

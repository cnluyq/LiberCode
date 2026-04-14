"""
LiberCode - AI Agent for Real Tasks with Teams

Multi-agent architecture for collaborative task execution.
"""

__version__ = "0.2.0"
__author__ = "YQ"

from libercode.config import Config
from libercode.taskboard.manager import TaskManager
from libercode.taskboard.models import Task, TaskStatus
from libercode.messaging.bus import MessageBus
from libercode.messaging.protocol import Message, MessageType

__all__ = [
    "Config",
    "TaskManager",
    "Task",
    "TaskStatus",
    "MessageBus",
    "Message",
    "MessageType",
]

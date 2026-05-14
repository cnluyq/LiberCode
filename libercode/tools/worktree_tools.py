"""
Worktree tools for LiberCode.

Provides tool definitions and handlers for worktree operations.
"""

import json
from typing import Dict, Callable
from libercode.worktree.manager import WorktreeManager
from libercode.taskboard.manager import TaskManager
from libercode.worktree.events import EventBus


def get_worktree_tools() -> list:
    """
    Get worktree tool definitions.

    Returns:
        List of 7 worktree tool definitions in Anthropic format
    """
    return [
        {
            "name": "worktree_create",
            "description": "Create a git worktree and optionally bind it to a task.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Unique worktree name"},
                    "task_id": {"type": "integer", "description": "Optional task ID to bind"},
                    "base_ref": {"type": "string", "description": "Git ref to branch from (default: HEAD)"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "worktree_list",
            "description": "List all worktrees tracked in .worktrees/index.json.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "worktree_status",
            "description": "Show git status for a worktree.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "worktree_run",
            "description": "Run a shell command in a worktree directory.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "command": {"type": "string"},
                },
                "required": ["name", "command"],
            },
        },
        {
            "name": "worktree_remove",
            "description": "Remove a worktree and optionally complete its bound task.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "force": {"type": "boolean"},
                    "complete_task": {"type": "boolean"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "worktree_keep",
            "description": "Mark a worktree as kept (manual review needed).",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "worktree_events",
            "description": "List recent worktree/task lifecycle events.",
            "input_schema": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
            },
        },
        {
            "name": "task_bind_worktree",
            "description": "Bind a task to a worktree name.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "worktree": {"type": "string"},
                    "owner": {"type": "string"},
                },
                "required": ["task_id", "worktree"],
            },
        },
    ]


def create_worktree_tool_handlers(
    worktree_manager: WorktreeManager,
    task_manager: TaskManager,
    event_bus: EventBus,
) -> Dict[str, Callable]:
    """
    Create worktree tool handlers.

    Args:
        worktree_manager: WorktreeManager instance
        task_manager: TaskManager instance
        event_bus: EventBus instance

    Returns:
        Dict mapping tool names to handler functions
    """
    def handle_worktree_create(**kwargs):
        entry = worktree_manager.create(
            kwargs["name"],
            kwargs.get("task_id"),
            kwargs.get("base_ref", "HEAD"),
        )
        return json.dumps(entry, indent=2, ensure_ascii=False)

    def handle_worktree_list(**kwargs):
        return worktree_manager.list_all()

    def handle_worktree_status(**kwargs):
        return worktree_manager.status(kwargs["name"])

    def handle_worktree_run(**kwargs):
        return worktree_manager.run(kwargs["name"], kwargs["command"])

    def handle_worktree_remove(**kwargs):
        return worktree_manager.remove(
            kwargs["name"],
            kwargs.get("force", False),
            kwargs.get("complete_task", False),
        )

    def handle_worktree_keep(**kwargs):
        entry = worktree_manager.keep(kwargs["name"])
        return json.dumps(entry, indent=2, ensure_ascii=False)

    def handle_worktree_events(**kwargs):
        return event_bus.list_recent(kwargs.get("limit", 20))

    def handle_task_bind_worktree(**kwargs):
        from libercode.taskboard.models import TaskStatus
        task = task_manager.update(
            kwargs["task_id"],
            worktree=kwargs["worktree"],
            owner=kwargs.get("owner"),
        )
        return json.dumps(task.to_dict(), indent=2, ensure_ascii=False)

    return {
        "worktree_create": handle_worktree_create,
        "worktree_list": handle_worktree_list,
        "worktree_status": handle_worktree_status,
        "worktree_run": handle_worktree_run,
        "worktree_remove": handle_worktree_remove,
        "worktree_keep": handle_worktree_keep,
        "worktree_events": handle_worktree_events,
        "task_bind_worktree": handle_task_bind_worktree,
    }

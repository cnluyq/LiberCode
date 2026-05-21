"""
Lead agent tools for LiberCode.

Provides tool definitions and handlers for the Lead agent.
"""
from typing import Dict, Callable
from libercode.taskboard.manager import TaskManager
from libercode.messaging.bus import MessageBus


def get_lead_tools() -> list:
    """
    Get Lead agent tool definitions.

    Returns:
        List of 12 tool definitions in Anthropic format
    """
    return [
        {
            "name": "bash",
            "description": "Run a shell command.",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
        {
            "name": "read_file",
            "description": "Read file contents.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "write_file",
            "description": "Write content to file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "edit_file",
            "description": "Replace exact text in file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
        {
            "name": "task_create",
            "description": "Create a new task. Must specify which role should handle it and who it is assigned to.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                    "required_role": {"type": "string", "description": "Required teammate role (e.g. 'frontend', 'backend')"},
                    "assigned_to": {"type": "string", "description": "Specific teammate name assigned to this task"},
                },
                "required": ["subject", "required_role", "assigned_to"],
            },
        },
        {
            "name": "task_update",
            "description": "Update a task's status or dependencies.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed"],
                    },
                    "addBlockedBy": {"type": "array", "items": {"type": "integer"}},
                    "addBlocks": {"type": "array", "items": {"type": "integer"}},
                    "required_role": {"type": "string", "description": "Required teammate role (e.g. 'frontend', 'backend')"},
                    "assigned_to": {"type": "string", "description": "Specific teammate name assigned to this task"},
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "task_list",
            "description": "List all tasks with status summary.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "task_get",
            "description": "Get full details of a task by ID.",
            "input_schema": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
        {
            "name": "spawn_teammate",
            "description": "Spawn an autonomous teammate.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["name", "role", "prompt"],
            },
        },
        {
            "name": "list_teammates",
            "description": "List all teammates.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "send_message",
            "description": "Send a message to a teammate.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "content": {"type": "string"},
                    "msg_type": {"type": "string", "enum": ["message", "notification", "broadcast"]},
                },
                "required": ["to", "content"],
            },
        },
        {
            "name": "read_inbox",
            "description": "Read and drain the lead's inbox.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "broadcast",
            "description": "Send a message to all teammates.",
            "input_schema": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            },
        },
        {
            "name": "shutdown_request",
            "description": "Request a teammate to shut down.",
            "input_schema": {
                "type": "object",
                "properties": {"teammate": {"type": "string"}},
                "required": ["teammate"],
          },
        },
        {
            "name": "plan_approval_response",
            "description": "Approve or reject a teammate's plan. Provide the teammate name, request_id, approve flag, and optional feedback.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Teammate name to send the response to"},
                    "request_id": {"type": "string"},
                    "approve": {"type": "boolean"},
                    "feedback": {"type": "string"},
                },
                "required": ["to", "request_id", "approve"],
          },
        },
        {
            "name": "drop_teammate",
            "description": "Drop (remove) a teammate and clean up their data. Use this when a teammate has shut down by itself and needs cleanup.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        {
            "name": "request_user_input",
            "description": "Request user intervention. Use this when you need the user to provide input, confirm an action, authorize an operation, or make a decision that requires human judgment. The user's response will be returned to you.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why user intervention is needed (e.g. 'Need confirmation to deploy to production', 'Need API key for service X')"},
                    "question": {"type": "string", "description": "The specific question or prompt to present to the user"},
                    "urgency": {"type": "string", "enum": ["low", "medium", "high"], "description": "How urgently the user needs to respond"},
                },
                "required": ["reason", "question"],
            },
        },
    ]


def create_lead_tool_handlers(
    task_manager: TaskManager,
    message_bus: MessageBus,
    teammate_manager, # TeammateManager type
) -> Dict[str, Callable]:
    """
    Create Lead agent tool handlers.

    Args:
        task_manager: TaskManager instance
        message_bus: MessageBus instance
        teammate_manager: TeammateManager instance

    Returns:
        Dict mapping tool names to handler functions
    """
    from libercode.tools.base import (
        run_bash,
        read_file,
        write_file,
        edit_file,
    )
    from libercode.messaging.protocol import Message, MessageType
    import json
    import uuid

    def handle_bash(**kwargs):
        return run_bash(kwargs["command"])

    def handle_read_file(**kwargs):
        return read_file(kwargs["path"], kwargs.get("limit"))

    def handle_write_file(**kwargs):
        return write_file(kwargs["path"], kwargs["content"])

    def handle_edit_file(**kwargs):
        return edit_file(kwargs["path"], kwargs["old_text"], kwargs["new_text"])

    def handle_task_create(**kwargs):
        task = task_manager.create(kwargs["subject"], kwargs.get("description", ""), kwargs["required_role"], kwargs["assigned_to"])
        return json.dumps(task.to_dict(), indent=2, ensure_ascii=False)

    def handle_task_update(**kwargs):
        from libercode.taskboard.models import TaskStatus

        status = None
        if "status" in kwargs:
            status = TaskStatus(kwargs["status"])

        task = task_manager.update(
            kwargs["task_id"],
            status=status,
            add_blocked_by=kwargs.get("addBlockedBy"),
            add_blocks=kwargs.get("addBlocks"),
            required_role=kwargs.get("required_role"),
            assigned_to=kwargs.get("assigned_to"),
        )
        return json.dumps(task.to_dict(), indent=2, ensure_ascii=False)

    def handle_task_list(**kwargs):
        return task_manager.list_all()

    def handle_task_get(**kwargs):
        task = task_manager.get(kwargs["task_id"])
        return json.dumps(task.to_dict(), indent=2, ensure_ascii=False)

    def handle_spawn_teammate(**kwargs):
        prompt_message={"role": "user", "content": kwargs["prompt"]}
        return teammate_manager.spawn(
            kwargs["name"], kwargs["role"], prompt_message
        )

    def handle_list_teammates(**kwargs):
        return teammate_manager.list_all()

    def handle_send_message(**kwargs):
        from libercode.messaging.protocol import Message

        msg_type = MessageType(kwargs.get("msg_type", "message"))
        msg = Message(
            type=msg_type,
            sender="lead",
            content=kwargs["content"],
        )
        return message_bus.send(msg, to=kwargs["to"])

    def handle_read_inbox(**kwargs):
        messages = message_bus.read_inbox("lead")
        return json.dumps([m.to_dict() for m in messages], indent=2, ensure_ascii=False)

    def handle_broadcast(**kwargs):
        return message_bus.broadcast(
            sender="lead",
            content=kwargs["content"],
            teammates=teammate_manager.member_names(),
        )

    def handle_shutdown_request(**kwargs):
        teammate = kwargs["teammate"]
        req_id = str(uuid.uuid4())[:8]
        msg = Message(
            type=MessageType.SHUTDOWN_REQUEST,
            sender="lead",
            content="Please shut down gracefully.",
            extra={"request_id": req_id},
        )
        message_bus.send(msg, to=teammate)
        return f"Shutdown request {req_id} sent to '{teammate}'"

    def handle_plan_approval_response(**kwargs):
        to = kwargs["to"]
        request_id = kwargs["request_id"]
        approve = kwargs["approve"]
        feedback = kwargs.get("feedback", "")
        msg = Message(
            type=MessageType.PLAN_APPROVAL_RESPONSE,
            sender="lead",
            content=feedback,
            extra={"request_id": request_id, "approve": approve, "feedback": feedback},
        )
        message_bus.send(msg, to=to)
        status = "approved" if approve else "rejected"
        return f"Plan {status} for '{to}'"

    def handle_drop_teammate(**kwargs):
        return teammate_manager.drop(kwargs["name"])

    def handle_request_user_input(**kwargs):
        import uuid

        request_id = str(uuid.uuid4())[:8]
        reason = kwargs["reason"]
        question = kwargs["question"]
        urgency = kwargs.get("urgency", "medium")

        return json.dumps({
            "request_id": request_id,
            "status": "waiting_for_user",
            "reason": reason,
            "question": question,
            "urgency": urgency,
        }, ensure_ascii=False)

    return {
        "bash": handle_bash,
        "read_file": handle_read_file,
        "write_file": handle_write_file,
        "edit_file": handle_edit_file,
        "task_create": handle_task_create,
        "task_update": handle_task_update,
        "task_list": handle_task_list,
        "task_get": handle_task_get,
        "spawn_teammate": handle_spawn_teammate,
        "list_teammates": handle_list_teammates,
        "send_message": handle_send_message,
        "read_inbox": handle_read_inbox,
        "broadcast": handle_broadcast,
        "plan_approval_response": handle_plan_approval_response,
        "shutdown_request": handle_shutdown_request,
        "drop_teammate": handle_drop_teammate,
        "request_user_input": handle_request_user_input,
    }

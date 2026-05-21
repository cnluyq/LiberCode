"""
Teammate agent tools for LiberCode.

Provides tool definitions and handlers for Teammate agents.
"""
from typing import Dict, Callable
from libercode.taskboard.manager import TaskManager
from libercode.messaging.bus import MessageBus


def get_teammate_tools() -> list:
    """
    Get Teammate agent tool definitions.

    Returns:
        List of 13 tool definitions in Anthropic format
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
            "name": "send_message",
            "description": "Send message to a teammate(mostly to lead).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "content": {"type": "string"},
                    "msg_type": {"type": "string", "enum": ["message", "notification", "broadcast", "shutdown_response", "plan_approval_request"]},
                },
                "required": ["to", "content"],
            },
        },
        {
            "name": "read_inbox",
            "description": "Read and drain your inbox.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "shutdown_response",
            "description": "Respond to a shutdown request.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "approve": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["request_id", "approve"],
            },
        },
        {
            "name": "plan_approval_request",
            "description": "Submit a plan for lead approval.",
            "input_schema": {
                "type": "object",
                "properties": {"plan": {"type": "string"}},
                "required": ["plan"],
            },
        },
        {
            "name": "idle",
            "description": "Signal that you have no more work. Enters idle polling phase.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "claim_task",
            "description": "Claim a task from the task board by ID.",
            "input_schema": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
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
            "name": "request_user_input",
            "description": "Request user intervention. Use this when you need the user to provide input, confirm an action, authorize an operation, or make a decision that requires human judgment. The request will be forwarded to the lead, and the user's response will be returned to you.",
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


def create_teammate_tool_handlers(
    task_manager: TaskManager,
    message_bus: MessageBus,
    sender_name: str,
    teammate=None,
) -> Dict[str, Callable]:
    """
    Create Teammate agent tool handlers.

    Args:
        task_manager: TaskManager instance
        message_bus: MessageBus instance
        sender_name: Name of this teammate

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

    def handle_bash(**kwargs):
        return run_bash(kwargs["command"])

    def handle_read_file(**kwargs):
        return read_file(kwargs["path"], kwargs.get("limit"))

    def handle_write_file(**kwargs):
        return write_file(kwargs["path"], kwargs["content"])

    def handle_edit_file(**kwargs):
        return edit_file(kwargs["path"], kwargs["old_text"], kwargs["new_text"])

    def handle_send_message(**kwargs):
        msg_type = MessageType(kwargs.get("msg_type", "message"))
        msg = Message(
            type=msg_type,
            sender=sender_name,
            content=kwargs["content"],
        )
        return message_bus.send(msg, to=kwargs["to"])

    def handle_read_inbox(**kwargs):
        messages = message_bus.read_inbox(sender_name)
        return json.dumps([m.to_dict() for m in messages], indent=2, ensure_ascii=False)

    def handle_shutdown_response(**kwargs):
        msg = Message(
            type=MessageType.SHUTDOWN_RESPONSE,
            sender=sender_name,
            content=kwargs.get("reason", ""),
            extra={
                "request_id": kwargs["request_id"],
                "approve": kwargs["approve"],
            },
        )
        message_bus.send(msg, to="lead")
        if kwargs.get("approve") and teammate:
            teammate._should_shutdown = True
        return "Shutdown response sent."

    def handle_plan_approval_request(**kwargs):
        import uuid

        request_id = str(uuid.uuid4())[:8]
        msg = Message(
            type=MessageType.PLAN_APPROVAL_REQUEST,
            sender=sender_name,
            content=kwargs["plan"],
            extra={"request_id": request_id, "plan": kwargs["plan"]},
        )
        message_bus.send(msg, to="lead")
        return f"Plan submitted (request_id={request_id}). Waiting for approval."

    def handle_idle(**kwargs):
        return "Entering idle phase. Will poll for new tasks."

    def handle_claim_task(**kwargs):
        import uuid

        if not teammate:
            return json.dumps({"error": "Teammate context not available"}, ensure_ascii=False)
        try:
            task_id = kwargs["task_id"]
            task = task_manager.get(task_id)
            task_data = task.to_dict()

            if task_data.get("status") != "pending":
                return json.dumps({"error": f"Task is not pending (status: {task_data.get('status')})"}, ensure_ascii=False)

            if task_data.get("owner"):
                return json.dumps({"error": f"Task already claimed by {task_data.get('owner')}"}, ensure_ascii=False)

            if task_data.get("blockedBy"):
                return json.dumps({"error": f"Task is blocked by {task_data.get('blockedBy')}"}, ensure_ascii=False)

            assigned_to = task_data.get("assigned_to")
            if assigned_to and assigned_to != teammate.name:
                return json.dumps({"error": f"Task is assigned to {assigned_to}"}, ensure_ascii=False)

            required_role = task_data.get("required_role", "")
            if required_role and required_role != teammate.role:
                return json.dumps({"error": f"Role mismatch. Task requires {required_role}, you are {teammate.role}"}, ensure_ascii=False)

            success = teammate._claim_task(task_data)
            if success:
                return json.dumps({"success": True, "message": f"Claimed task #{task_id}"}, ensure_ascii=False)
            else:
                return json.dumps({"error": "Failed to claim task"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def handle_task_list(**kwargs):
        return task_manager.list_all()

    def handle_task_get(**kwargs):
        task = task_manager.get(kwargs["task_id"])
        return json.dumps(task.to_dict(), indent=2, ensure_ascii=False)

    def handle_request_user_input(**kwargs):
        import uuid

        request_id = str(uuid.uuid4())[:8]
        reason = kwargs["reason"]
        question = kwargs["question"]
        urgency = kwargs.get("urgency", "medium")

        msg = Message(
            type=MessageType.USER_INPUT_REQUEST,
            sender=sender_name,
            content=question,
            extra={
                "request_id": request_id,
                "reason": reason,
                "urgency": urgency,
            },
        )
        message_bus.send(msg, to="lead")

        return json.dumps({
            "request_id": request_id,
            "status": "forwarded_to_lead",
            "reason": reason,
            "question": question,
            "urgency": urgency,
        }, ensure_ascii=False)

    return {
        "bash": handle_bash,
        "read_file": handle_read_file,
        "write_file": handle_write_file,
        "edit_file": handle_edit_file,
        "send_message": handle_send_message,
        "read_inbox": handle_read_inbox,
        "shutdown_response": handle_shutdown_response,
        "plan_approval_request": handle_plan_approval_request,
        "idle": handle_idle,
        "claim_task": handle_claim_task,
        "task_list": handle_task_list,
        "task_get": handle_task_get,
        "request_user_input": handle_request_user_input,
    }

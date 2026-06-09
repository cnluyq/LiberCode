"""Lead agent for LiberCode.

Main orchestrator that manages tasks and teammates.
"""

import asyncio
import json
import time
import threading
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from anthropic import Anthropic
from anthropic import AsyncAnthropic
from pathlib import Path

from libercode.config import Config
from libercode.taskboard.manager import TaskManager
from libercode.messaging.bus import MessageBus
from libercode.messaging.serialization import serialize_content
from libercode.utils.token_tracker import TokenTracker
from libercode.utils.logging import get_logger, log_task_event, log_agent_event
from libercode.ui.output import tprint, format_llm_response
from libercode.exceptions import LLMInternalError, LLMRateLimitError
from libercode.core.interrupt_handler import check_cancel, request_cancel, clear_cancel


@dataclass
class LeadAgent:
    """Lead agent that orchestrates tasks and teammates.

    Attributes:
    client: Anthropic client
    async_client: Async Anthropic client for interruptible calls
    config: Config instance
    message_bus: MessageBus instance
    task_manager: TaskManager instance
    teammate_manager: TeammateManager instance
    token_tracker: Token usage tracker
    messages: Conversation history
    """

    client: Anthropic
    async_client: AsyncAnthropic
    config: Config
    message_bus: MessageBus
    task_manager: TaskManager
    teammate_manager: Any # TeammateManager
    token_tracker: TokenTracker = field(default_factory=TokenTracker)
    messages: List[Dict] = field(default_factory=list)
    _input_counter: int = field(default=0, init=False)
    _agent_counter: int = field(default=0, init=False)
    _logger: Any = field(default=None, init=False)
    _agents_md_injected: bool = field(default=False, init=False)
    _user_input_event: Any = field(default=None, init=False)
    _user_input_request_id: Any = field(default=None, init=False)
    _user_input_response: Any = field(default=None, init=False)
    real_time_model_id: str = ""

    def __post_init__(self):
        """Initialize logger after dataclass init."""
        self._logger = get_logger('libercode.lead', component='lead')
        self._agents_md_injected = False
        self._user_input_event = asyncio.Event()
        self._user_input_request_id = None
        self._user_input_response = None

    def _load_agents_md(self) -> Optional[str]:
        """Load AGENTS.md or CLAUDE.md from project root."""
        search_paths = [
            self.config.workdir / "AGENTS.md",
            self.config.workdir / "CLAUDE.md",
        ]
        for path in search_paths:
            if path.exists():
                self._logger.info(f"Loading instructions from {path.name}")
                return path.read_text(encoding="utf-8")
        return None

    def _inject_agents_md(self) -> None:
        """Inject AGENTS.md content as initial user message if not already done."""
        if self._agents_md_injected:
            return
        agents_md_content = self._load_agents_md()
        if agents_md_content:
            self.messages.append({
                "role": "user",
                "content": f"<project_instructions>\n{agents_md_content}\n</project_instructions>"
            })
            self._agents_md_injected = True
            self._logger.info("Injected AGENTS.md/CLAUDE.md into conversation context")

    async def async_run_llm_loop(self) -> None:
        """Run LLM interaction loop with tool calling (async version with cancellation support)."""
        clear_cancel()
        while True:
            if check_cancel():
                self._logger.info("LLM loop cancelled by user before async LLM call")
                tprint("\n[Interrupted by user before async LLM call]")
                return

            self._agent_counter += 1

            inbox = self.message_bus.read_inbox("lead")
            if inbox:
                self._logger.debug(f"Received {len(inbox)} inbox messages")
                from libercode.messaging.protocol import MessageType, Message

                user_input_msgs = [m for m in inbox if m.type == MessageType.USER_INPUT_REQUEST]
                other_msgs = [m for m in inbox if m.type != MessageType.USER_INPUT_REQUEST]

                for omsg in other_msgs:
                    self._logger.info(f"Lead received message<{omsg.type}> from {omsg.sender} during work")
                    self.messages.append({"role": "user", "content": "Note the inbox message"})
                    self.messages.append({
                        "role": "user",
                        "content": f"<inbox>{json.dumps(omsg.to_dict(), ensure_ascii=False)}</inbox>"})

                for umsg in user_input_msgs:
                    self._logger.info(f"Lead received message<{umsg.type}> from {umsg.sender} during work")
                    req_id = umsg.extra.get("request_id", "")
                    reason = umsg.extra.get("reason", "")
                    question = umsg.content
                    urgency = umsg.extra.get("urgency", "medium")

                    user_response = await self._prompt_user_input(
                        request_id=req_id,
                        reason=reason,
                        question=question,
                        urgency=urgency,
                        header=f"USER INPUT REQUIRED (from {umsg.sender})",
                    )
                    user_response = user_response or "skipped"

                    resp_msg = Message(
                        type=MessageType.USER_INPUT_RESPONSE,
                        sender="lead",
                        content=user_response,
                        extra={"request_id": req_id},
                    )
                    self.message_bus.send(resp_msg, to=umsg.sender)

            self._logger.info(f"user_input#{self._input_counter} round#{self._agent_counter} calling LLM (async)......")
            start_time = time.time()
            try:
                response = await self.async_client.messages.create(
                    model=self.config.model_id,
                    system=self._get_system_prompt(),
                    messages=self.messages,
                    tools=self._get_tools(),
                    max_tokens=8000,
                )
                self.real_time_model_id = response.model
                duration_ms = int((time.time() - start_time) * 1000)

                self._logger.info(
                    f"LLM call: model={response.model}, "
                    f"tokens={response.usage.input_tokens}in/{response.usage.output_tokens}out, "
                    f"duration={duration_ms}ms"
                )

            except asyncio.CancelledError:
                self._logger.info("LLM call cancelled during async LLM call")
                tprint("\n[Interrupted by user during async LLM call]")
                return
            except Exception as e:
                if hasattr(e, 'status_code'):
                    if e.status_code == 500 or e.status_code == 502 or e.status_code == 503:
                        self._logger.warning(f"LLM internal error({e.status_code}), sleeping and retry")
                        await asyncio.sleep(30)
                        continue
                    elif e.status_code == 429:
                        self._logger.warning("Rate limit exceeded, sleeping and retrying")
                        await asyncio.sleep(30)
                        continue
                self._logger.error(f"Exception during LLM call: {e}")
                tprint(f"Exception happened: {e}")
                return

            if check_cancel():
                self._logger.info("LLM loop cancelled by user after async LLM call")
                tprint("\n[Interrupted by user after async LLM call]")
                return

            self.token_tracker.record("lead", response, duration_ms)

            if hasattr(response, "model_dump"):
                response_dict = response.model_dump()
                self._logger.info(f"LLM response: \n{json.dumps(response_dict, indent=2, ensure_ascii=False)}")
            else:
                self._logger.info(f"LLM response (raw): \n{response}")

            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                if self._has_active_teammates():
                    self._logger.info(f"LLM stop_reason is '{response.stop_reason}' but teammates still active, continuing monitoring")
                    await asyncio.sleep(self.config.poll_interval)
                    self.messages.append({"role": "user", "content": "Still teammates working, keep monitoring. Check inbox for new messages and review task/teammate status."})
                    continue
                self._logger.debug(f"LLM loop returned as stop_reason ({response.stop_reason}) is not 'tool_use'.")
                return

            format_llm_response(response, "team lead")

            results = []
            user_input_request_data = None
            for block in response.content:
                if block.type == "tool_use":
                    output = self._execute_tool(block.name, block.input)

                    self._logger.info(f"Executing tool: {block.name}, result:\n{str(output)}")

                    if block.name == "request_user_input":
                        user_input_request_data = output

                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output),
                    })

            self.messages.append({"role": "user", "content": results})

            if user_input_request_data:
                user_response = await self._wait_for_user_input(user_input_request_data)
                if user_response is not None:
                    import json as _json
                    resp_data = _json.loads(user_input_request_data) if isinstance(user_input_request_data, str) else user_input_request_data
                    request_id = resp_data.get("request_id", "")
                    self.messages.append({
                        "role": "user",
                        "content": f"<user_input_response request_id=\"{request_id}\">{user_response}</user_input_response>",
                    })

    async def _prompt_user_input(self, request_id: str, reason: str, question: str, urgency: str = "medium", header: str = "USER INPUT REQUIRED") -> Optional[str]:
        """Display prompt and wait for user input via CLI.

        Args:
            request_id: Unique ID for this request
            reason: Why user intervention is needed
            question: The question to present to the user
            urgency: low/medium/high
            header: Header line for the prompt display

        Returns:
            User's response text, or None if cancelled
        """
        urgency_tag = f"[{urgency.upper()}] " if urgency != "medium" else ""
        tprint(f"\n{'='*60}")
        tprint(f"{header} {urgency_tag}")
        tprint(f"Reason: {reason}")
        tprint(f"Question: {question}")
        tprint(f"Request ID: {request_id}")
        tprint(f"{'='*60}")
        tprint("Type your response below (or 'skip' to decline):")

        self._user_input_event.clear()
        self._user_input_request_id = request_id
        self._user_input_response = None

        await self._user_input_event.wait()

        self._user_input_request_id = None
        return self._user_input_response

    async def _wait_for_user_input(self, tool_output: str) -> Optional[str]:
        """Pause the LLM loop and wait for user to provide input.

        Args:
            tool_output: JSON output from request_user_input tool

        Returns:
            User's response text, or None if cancelled
        """
        import json as _json
        data = _json.loads(tool_output) if isinstance(tool_output, str) else tool_output
        return await self._prompt_user_input(
            request_id=data.get("request_id", ""),
            reason=data.get("reason", ""),
            question=data.get("question", ""),
            urgency=data.get("urgency", "medium"),
            header="USER INPUT REQUIRED",
        )

    def provide_user_input(self, response: str) -> None:
        """Provide user input response from CLI. Called by the REPL loop.

        Args:
            response: User's response text
        """
        self._user_input_response = response
        self._user_input_event.set()

    def _get_system_prompt(self) -> str:
        """Get system prompt for lead agent."""
        prompt_path = Path(__file__).parent.parent / "prompts" / "lead_system.txt"
        lead_sys_prompt = prompt_path.read_text(encoding="utf-8")
        return lead_sys_prompt.format(workdir=self.config.workdir)

    def _get_tools(self) -> List[Dict]:
        """Get lead agent tools."""
        from libercode.tools.lead_tools import get_lead_tools
        return get_lead_tools()

    def _has_active_teammates(self) -> bool:
        """Check if any teammates are still working or idle."""
        for name in self.teammate_manager.member_names():
            member = self.teammate_manager._find_member(name)
            if member and member.get("status") in ("working", "idle"):
                return True
        return False

    def clear_messages(self) -> None:
        """Clear lead agent's message history."""
        self.messages.clear()
        self._logger.info("Lead message history cleared")

    def _execute_tool(self, tool_name: str, args: dict) -> str:
        """Execute a tool with validation."""
        from libercode.tools.lead_tools import create_lead_tool_handlers, get_lead_tools
        from libercode.tools.validator import validate_and_fix_args

        tools = get_lead_tools()
        fixed_args, validation_error, validation_warning = validate_and_fix_args(tool_name, args, tools)
        if validation_error:
            self._logger.warning(f"Tool args validation failed: {validation_error}")
            return validation_error

        handlers = create_lead_tool_handlers(
            task_manager=self.task_manager,
            message_bus=self.message_bus,
            teammate_manager=self.teammate_manager,
        )

        args_substr = str(fixed_args)[:100] + ("..." if len(str(fixed_args)) > 100 else "")
        tprint(f"Executing tool: {tool_name}, args: {args_substr}")
        self._logger.info(f"Executing tool: {tool_name}, args:\n{fixed_args}")
        handler = handlers.get(tool_name)
        if not handler:
            return f"Unknown tool: {tool_name}"
        try:
            result = handler(**fixed_args)
        except Exception as e:
            self._logger.error(f"Tool {tool_name} failed: {e}")
            result = f"Error: {e}"

        if validation_warning:
            result = f"{result}\n\n{validation_warning}"
        return result

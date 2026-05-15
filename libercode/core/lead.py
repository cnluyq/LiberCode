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
from libercode.utils.logging import get_logger, log_task_event, log_agent_event, log_llm_call
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

    def __post_init__(self):
        """Initialize logger after dataclass init."""
        self._logger = get_logger('libercode.lead', component='lead')
        self._agents_md_injected = False

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

    def process_user_input(self, query: str) -> None:
        """Process user input through LLM loop.

        Args:
        query: User input string
        """
        self._inject_agents_md()
        self.messages.append({"role": "user", "content": query})

        self._input_counter += 1
        self._agent_counter = 0

        self._logger.info(f"Processing user input (round#{self._input_counter})")

        if self.config.debug:
            tprint(f"<<<<<< [teammate lead] history input (round#{self._input_counter}) {time.strftime('%Y-%m-%d %H:%M:%S')} >>>>>>")
            history_serialized = serialize_content(self.messages)
            tprint(json.dumps(history_serialized, indent=2, ensure_ascii=False))

        self._run_llm_loop()

        if self.config.debug:
            tprint(f"<<<<<< [teammate lead] history output (round#{self._input_counter}) {time.strftime('%Y-%m-%d %H:%M:%S')} >>>>>>")
            history_serialized = serialize_content(self.messages)
            tprint(json.dumps(history_serialized, indent=2, ensure_ascii=False))

        self._logger.info(f"Completed processing round#{self._input_counter}")

    def _run_llm_loop(self) -> None:
        """Run LLM interaction loop with tool calling."""
        while True:
            self._agent_counter += 1

            inbox = self.message_bus.read_inbox("lead")
            if inbox:
                self._logger.debug(f"Received {len(inbox)} inbox messages")
                inbox_data = [msg.to_dict() for msg in inbox]
                self.messages.append({
                    "role": "user",
                    "content": f"<inbox>{json.dumps(inbox_data, indent=2, ensure_ascii=False)}</inbox>",
                })
                self.messages.append({
                    "role": "assistant",
                    "content": "Noted inbox messages.",
                })

            self._logger.info(f"user_input#{self._input_counter} round#{self._agent_counter} calling LLM ......")
            start_time = time.time()
            try:
                response = self.client.messages.create(
                    model=self.config.model_id,
                    system=self._get_system_prompt(),
                    messages=self.messages,
                    tools=self._get_tools(),
                    max_tokens=8000,
                )
                duration_ms = int((time.time() - start_time) * 1000)

                log_llm_call(
                    agent='lead',
                    model=response.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    duration_ms=duration_ms
                )

            except Exception as e:
                if hasattr(e, 'status_code'):
                    if e.status_code == 500 or e.status_code == 502 or e.status_code == 503:
                        self._logger.warning("LLM internal error, sleeping and retrying")
                        time.sleep(30)
                        continue
                    elif e.status_code == 429:
                        self._logger.warning("Rate limit exceeded, sleeping and retrying")
                        time.sleep(30)
                        continue
                self._logger.error(f"Exception during LLM call: {e}")
                tprint(f"Fatal exception happened during LLM call and return")
                return

            self.token_tracker.record("lead", response, duration_ms)

            if hasattr(response, "model_dump"):
                response_dict = response.model_dump()
                self._logger.info(f"LLM response: \n{json.dumps(response_dict, indent=2, ensure_ascii=False)}")
            else:
                self._logger.info(f"LLM response (raw): \n{response}")

            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                self._logger.debug("LLM loop completed without tool use")
                return

            format_llm_response(response,"team lead")

            results = []
            for block in response.content:
                if block.type == "tool_use":
                    output = self._execute_tool(block.name, block.input)

                    self._logger.info(f"Executing tool: {block.name}, result:\n{str(output)}")
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output),
                    })

            self.messages.append({"role": "user", "content": results})

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
                inbox_data = [msg.to_dict() for msg in inbox]
                self.messages.append({
                    "role": "user",
                    "content": f"<inbox>{json.dumps(inbox_data, indent=2, ensure_ascii=False)}</inbox>",
                })
                self.messages.append({
                    "role": "assistant",
                    "content": "Noted inbox messages.",
                })

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
                duration_ms = int((time.time() - start_time) * 1000)

                log_llm_call(
                    agent='lead',
                    model=response.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    duration_ms=duration_ms
                )

            except asyncio.CancelledError:
                self._logger.info("LLM call cancelled during async LLM call")
                tprint("\n[Interrupted by user during async LLM call]")
                return
            except Exception as e:
                if hasattr(e, 'status_code'):
                    if e.status_code == 500 or e.status_code == 502:
                        self._logger.warning("LLM internal error, sleeping and retrying")
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
                self._logger.debug("LLM loop completed without tool use")
                return

            format_llm_response(response, "team lead")

            results = []
            for block in response.content:
                if block.type == "tool_use":
                    output = self._execute_tool(block.name, block.input)

                    self._logger.info(f"Executing tool: {block.name}, result:\n{str(output)}")
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output),
                    })

            self.messages.append({"role": "user", "content": results})

    def _get_system_prompt(self) -> str:
        """Get system prompt for lead agent."""
        prompt_path = Path(__file__).parent.parent / "prompts" / "lead_system.txt"
        lead_sys_prompt = prompt_path.read_text(encoding="utf-8")
        return lead_sys_prompt.format(workdir=self.config.workdir)

    def _get_tools(self) -> List[Dict]:
        """Get lead agent tools."""
        from libercode.tools.lead_tools import get_lead_tools
        return get_lead_tools()

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

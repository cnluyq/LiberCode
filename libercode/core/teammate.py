"""Teammate agent for LiberCode.

Autonomous worker that claims and executes tasks.
"""

import json
import time
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from anthropic import Anthropic
import threading

from libercode.config import Config
from libercode.taskboard.manager import TaskManager
from libercode.messaging.bus import MessageBus
from libercode.messaging.protocol import Message, MessageType
from libercode.messaging.serialization import serialize_content
from libercode.utils.token_tracker import TokenTracker
from libercode.utils.logging import get_logger, log_task_event, log_agent_event
from libercode.ui.output import tprint, format_llm_response

from pathlib import Path

from libercode.exceptions import TaskClaimError


@dataclass
class TeammateAgent:
    """Autonomous teammate agent.

    Attributes:
        name: Teammate name
        role: Teammate role
        client: Anthropic client
        config: Config instance
        message_bus: MessageBus instance
        task_manager: TaskManager instance
        token_tracker: Token usage tracker
        messages: Conversation history
        pty_file: Optional output file (for tmux)
    """

    name: str
    role: str
    client: Anthropic
    config: Config
    message_bus: MessageBus
    task_manager: TaskManager
    token_tracker: TokenTracker = field(default_factory=TokenTracker)
    messages: List[Dict] = field(default_factory=list)
    pty_file: Optional[Any] = None
    teammate_manager: Any = None
    _logger: Any = field(default=None, init=False)
    _should_shutdown: bool = False
    _agents_md_injected: bool = False
    real_time_model_id: str = ""

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
            self._logger.info("Injected AGENTS.md/CLAUDE.md into teammate context")

    def __post_init__(self):
        """Initialize logger after dataclass init."""
        self._logger = get_logger(f'libercode.teammate.{self.name}', component='teammate')

    def run(self, initial_message: dict, team_name: str = "default") -> None:
        """Main teammate loop.

        Args:
            initial_message: Starting message for LLM
            team_name: Team name
        """
        from libercode.ui.output import OutputManager
        output_manager = OutputManager()

        if self.pty_file is not None:
            output_manager.set_target(self.pty_file)

        try:
            log_agent_event(self.name, 'started', {'role': self.role})
            self._logger.info(f"Teammate {self.name} thread started, pty_file={self.pty_file}")

            prompt_path = Path(__file__).parent.parent / "prompts" / "teammate_system.txt"
            sys_prompt = prompt_path.read_text(encoding="utf-8")
            sys_prompt = sys_prompt.format(
                name=self.name,
                role=self.role,
                team_name=team_name,
                workdir=self.config.workdir
            )

            self._inject_agents_md()
            self.messages.append(initial_message)

            self._run_work_loop(sys_prompt, output_manager)
        finally:
            output_manager.set_target(None)
            if self.pty_file:
                try:
                    self.pty_file.close()
                except OSError:
                    pass

    def run_with_history(self, restored_messages: List[Dict], team_name: str = "default") -> None:
        """Main teammate loop with pre-restored message history (for session recovery).

        Args:
            restored_messages: Full message history to restore
            team_name: Team name
        """
        from libercode.ui.output import OutputManager
        output_manager = OutputManager()

        if self.pty_file is not None:
            output_manager.set_target(self.pty_file)

        try:
            log_agent_event(self.name, 'recovered', {'role': self.role})
            self._logger.info(f"Teammate {self.name} thread started (recovered), pty_file={self.pty_file}")

            prompt_path = Path(__file__).parent.parent / "prompts" / "teammate_system.txt"
            sys_prompt = prompt_path.read_text(encoding="utf-8")
            sys_prompt = sys_prompt.format(
                name=self.name,
                role=self.role,
                team_name=team_name,
                workdir=self.config.workdir
            )

            self.messages = list(restored_messages)
            self._agents_md_injected = True

            self._run_work_loop(sys_prompt, output_manager)
        finally:
            output_manager.set_target(None)
            if self.pty_file:
                try:
                    self.pty_file.close()
                except OSError:
                    pass

    def _handle_inbox_message(self, msg) -> None:
        """Handle a single inbox message based on its type."""
        if msg.type == MessageType.USER_INPUT_RESPONSE:
            req_id = msg.extra.get("request_id", "")
            self.messages.append({"role": "user", "content": "Note the inbox message"})
            self.messages.append({
                "role": "user",
                "content": f"<inbox><user_input_response request_id=\"{req_id}\">{msg.content}</user_input_response></inbox>"})
        else:
            self.messages.append({"role": "user", "content": "Note the inbox message"})
            self.messages.append({"role": "user", "content": f"<inbox>{json.dumps(msg.to_dict(), ensure_ascii=False)}</inbox>"})

    def _run_work_loop(self, sys_prompt: str, output_manager) -> None:
        """Core work/idle loop shared by run() and run_with_history()."""
        while True:
            # -- WORK PHASE: standard agent loop --
            # Check initial status: skip work loop if already idle
            initial_status = self._get_status()
            if initial_status != "idle":
                round_num = 0
                for _ in range(50):
                    round_num += 1

                    # Check inbox
                    inbox = self.message_bus.read_inbox(self.name)
                    if inbox:
                        for msg in inbox:
                            self._logger.info(f"Teammate {self.name} received message<{msg.type}> from {msg.sender} during work")
                            self._handle_inbox_message(msg)

                    # Call LLM
                    self._logger.info(f"round#{round_num} calling LLM ......")
                    start_time = time.time()
                    try:
                        response = self.client.messages.create(
                            model=self.config.model_id,
                            system=sys_prompt,
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

                    except Exception as e:
                        if hasattr(e, 'status_code'):
                            if e.status_code == 500 or e.status_code == 502 or e.status_code == 503:
                                self._logger.warning("LLM internal error({e.status_code}), sleeping and retry")
                                time.sleep(30)
                                continue
                            elif e.status_code == 429:
                                self._logger.warning("Rate limit exceeded, sleeping and retrying")
                                time.sleep(30)
                                continue
                        self._logger.error(f"Exception during LLM call: {e}")
                        tprint(f"Teammate {self.name} shut down because of a fatal internel exception happened")
                        msg = Message(
                            type=MessageType.SHUTDOWN_BY_SELF,
                            sender=self.name,
                            content=f"Teammate {self.name} shut down because of a fatal internel error",
                        )
                        self.message_bus.send(msg, to="lead")
                        return

                    # Update token stats
                    self.token_tracker.record(self.name, response, duration_ms)

                    if hasattr(response, "model_dump"):
                        response_dict = response.model_dump()
                        self._logger.info(f"LLM response: {json.dumps(response_dict, indent=2, ensure_ascii=False)}")
                    else:
                        self._logger.info(f"LLM response (raw): {response}")

                    self.messages.append({"role": "assistant", "content": response.content})

                    # Check if done
                    if response.stop_reason != "tool_use":
                        self._logger.info(f"Teammate {self.name} llm loop go to idle as stop_reason ({response.stop_reason}) is not 'tool_use'.")
                        break

                    format_llm_response(response, self.name)

                    # Execute tools
                    results = []
                    idle_requested = False
                    for block in response.content:
                        if block.type == "tool_use":
                            if block.name == "idle":
                                idle_requested = True
                                output = "Entering idle phase. Will poll for new tasks."
                                log_agent_event(self.name, 'idle', {'round': round_num})
                                self._logger.debug("Entering idle phase")
                            else:
                                output = self._execute_tool(block.name, block.input)

                            self._logger.info(f"Executing tool: {block.name}, result:\n{str(output)}")
                            results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": str(output),
                            })

                    if self._should_shutdown:
                        log_agent_event(self.name, 'shutdown', {'reason': 'approved'})
                        self._logger.info(f"Teammate {self.name} shut down completely and quitted")
                        tprint(f"Teammate {self.name} shut down completely and quitted")
                        return

                    self.messages.append({"role": "user", "content": results})

                    if idle_requested:
                        self._set_status("idle")
                        break

            # -- IDLE PHASE: poll for inbox messages and unclaimed tasks --
            tprint(f"Teammate {self.name} entering state of idle")
            self._logger.info(f"Teammate {self.name} entering state of idle")
            resume = False
            polls = self.config.idle_timeout // max(self.config.poll_interval, 1)
            for _ in range(polls):
                time.sleep(self.config.poll_interval)

                # Check inbox
                inbox = self.message_bus.read_inbox(self.name)
                if inbox:
                    for msg in inbox:
                        self._logger.info(f"Teammate {self.name} received message<{msg.type}> from {msg.sender} during idle")
                        self._handle_inbox_message(msg)
                        resume = True

                if resume:
                    self._set_status("working")
                    break

                # Check for unclaimed tasks
                unclaimed = self.task_manager.scan_unclaimed_tasks(self.role, self.name)
                if unclaimed:
                    task_data = unclaimed[0]
                    task_id = task_data["id"]
                    # Try to claim task
                    try:
                        claimed = self.task_manager.claim_task(task_id, self.name)
                        if claimed:
                            task_prompt = (
                                f"<auto-claimed>Task #{claimed.id}: \n"
                                f"Subject: {claimed.subject}\n"
                                f"Description: {claimed.description}\n"
                                f"</auto-claimed>\n"
                                f"Note: When the task complete, send message to 'lead' with task-id and status to notify lead to update task status. Then use idle tool to back to idle state."
                            )
                            self.messages.append({"role": "user", "content": task_prompt})

                            self._logger.info(f"Claimed task: {task_prompt}")
                            resume = True
                            self._set_status("working")
                            break
                    except Exception:
                        continue

            if not resume:
                # No work found, shutdown
                log_agent_event(self.name, 'shutdown', {'reason': 'no_work'})
                self._logger.info(f"Teammate {self.name} shutting down: no work found")
                tprint(f"Teammate {self.name} shutting down by self: no work found")
                msg = Message(
                    type=MessageType.SHUTDOWN_BY_SELF,
                    sender=self.name,
                    content=f"Teammate {self.name} is shutting down because no work was found",
                )
                self.message_bus.send(msg, to="lead")
                return

    def _get_status(self) -> str:
        if not self.teammate_manager:
            return "working"
        member = self.teammate_manager._find_member(self.name)
        if not member:
            return "working"
        return member.get("status", "working")

    def _set_status(self, status: str) -> None:
        if self.teammate_manager:
            self.teammate_manager._set_status(self.name, status)

    def clear_messages(self) -> None:
        """Clear teammate's message history."""
        self.messages.clear()
        self._logger.info(f"Teammate {self.name} message history cleared")

    def _get_tools(self) -> List[Dict]:
        """Get teammate tools."""
        from libercode.tools.teammate_tools import get_teammate_tools
        return get_teammate_tools()

    def _execute_tool(self, tool_name: str, args: Dict) -> str:
        """Execute a tool."""
        from libercode.tools.teammate_tools import create_teammate_tool_handlers, get_teammate_tools
        from libercode.tools.validator import validate_and_fix_args

        tools = get_teammate_tools()
        fixed_args, validation_error, validation_warning = validate_and_fix_args(tool_name, args, tools)
        if validation_error:
            self._logger.warning(f"Tool args validation failed: {validation_error}")
            return validation_error

        handlers = create_teammate_tool_handlers(
            task_manager=self.task_manager,
            message_bus=self.message_bus,
            sender_name=self.name,
            teammate=self,
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

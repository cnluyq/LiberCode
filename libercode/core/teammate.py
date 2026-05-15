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
from libercode.utils.logging import get_logger, log_task_event, log_agent_event, log_llm_call
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
    _logger: Any = field(default=None, init=False)
    _should_shutdown: bool = False
    _agents_md_injected: bool = False

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
                self.pty_file.close()

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
                self.pty_file.close()

    def _run_work_loop(self, sys_prompt: str, output_manager) -> None:
        """Core work/idle loop shared by run() and run_with_history()."""
        while True:
            # -- WORK PHASE: standard agent loop --
            round_num = 0
            for _ in range(50):
                round_num += 1

                # Check inbox
                inbox = self.message_bus.read_inbox(self.name)
                for msg in inbox:
                    if msg.type == MessageType.SHUTDOWN_REQUEST:
                        self._logger.info(f"Teammate {self.name} received shutdown request during work")

                    self.messages.append({"role": "user", "content": json.dumps(msg.to_dict(), ensure_ascii=False)})

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
                    duration_ms = int((time.time() - start_time) * 1000)

                    # Log LLM call
                    log_llm_call(
                        agent=(f"teammate:{self.name}"),
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
                    break

            # -- IDLE PHASE: poll for inbox messages and unclaimed tasks --
            resume = False
            polls = self.config.idle_timeout // max(self.config.poll_interval, 1)
            for _ in range(polls):
                time.sleep(self.config.poll_interval)

                # Check inbox
                inbox = self.message_bus.read_inbox(self.name)
                if inbox:
                    for msg in inbox:
                        if msg.type == MessageType.SHUTDOWN_REQUEST:
                            self._logger.info(f"Teammate {self.name} received shutdown request during idle")

                        self.messages.append({"role": "user", "content": json.dumps(msg.to_dict(), ensure_ascii=False)})
                        resume = True

                if resume:
                    break

                # Check for unclaimed tasks
                unclaimed = self._scan_unclaimed_tasks()
                if unclaimed:
                    task = unclaimed[0]
                    # Try to claim task
                    try:
                        claimed = self._claim_task(task)
                        if claimed:
                            resume = True
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

    def _scan_unclaimed_tasks(self) -> List[Dict]:
        """Scan for unclaimed tasks matching teammate's role."""
        from libercode.taskboard.models import TaskStatus

        all_tasks = []
        for f in sorted(self.task_manager.tasks_dir.glob("task_*.json")):
            task_data = json.loads(f.read_text())
            if not (
                task_data.get("status") == "pending"
                and not task_data.get("owner")
                and not task_data.get("blockedBy")
            ):
                continue

            assigned_to = task_data.get("assigned_to")
            if assigned_to and assigned_to != self.name:
                continue

            required_role = task_data.get("required_role", "")
            if required_role and required_role != self.role:
                continue

            all_tasks.append(task_data)

        if all_tasks:
            self._logger.debug(f"Found {len(all_tasks)} unclaimed tasks matching role {self.role}")

        return all_tasks

    def _claim_task(self, task: Dict) -> bool:
        """Claim a task."""
        from libercode.taskboard.models import TaskStatus

        task_id = task["id"]
        task_file = self.task_manager.tasks_dir / f"task_{task_id}.json"

        if not task_file.exists():
            return False

        current_task = json.loads(task_file.read_text())

        # Check if still available
        if current_task.get("owner"):
            return False
        if current_task.get("status") != "pending":
            return False

        # Claim it
        current_task["owner"] = self.name
        current_task["status"] = "in_progress"
        task_file.write_text(json.dumps(current_task, indent=2, ensure_ascii=False))

        # Create task prompt
        task_prompt = (
            f"<auto-claimed>Task #{task['id']}: {task['subject']}\n"
            f"{task.get('description', '')}</auto-claimed>"
            f"When the task complete, send message to 'lead' with task-id and status to notify lead to update task status. Then use idle tool to back to idle state</auto-claimed>"
        )

        # Log task claim
        log_task_event(task_id, 'claimed', {'teammate': self.name})
        self._logger.info(f"Claimed task: {task_prompt}")

        # Add to messages
        if len(self.messages) <= 3:
            # Add identity block
            self.messages.insert(0, {
                "role": "user",
                "content": f"<identity>You are '{self.name}', role: {self.role}, team: default. Continue your work.</identity>",
            })
            self.messages.insert(1, {"role": "assistant", "content": f"I am {self.name}. Continuing."})

        self.messages.append({"role": "user", "content": task_prompt})
        self.messages.append({"role": "assistant", "content": f"Claimed task #{task['id']}. Working on it."})

        return True

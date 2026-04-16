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
from libercode.messaging.protocol import MessageType
from libercode.messaging.serialization import serialize_content
from libercode.utils.token_tracker import TokenTracker
from libercode.utils.logging import get_logger, log_task_event, log_agent_event, log_llm_call
from libercode.ui.output import tprint
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
    
    def __post_init__(self):
        """Initialize logger after dataclass init."""
        self._logger = get_logger(f'libercode.teammate.{self.name}', component='teammate')
    
    def run(self, initial_prompt: str, team_name: str = "default") -> None:
        """Main teammate loop.
        
        Args:
            initial_prompt: Starting prompt
            team_name: Team name
        """
        # Set thread output if provided
        from libercode.ui.output import OutputManager
        output_manager = OutputManager()
        
        if self.pty_file is not None:
            output_manager.set_target(self.pty_file)
        
        try:
            log_agent_event(self.name, 'started', {'role': self.role})
            self._logger.info(f"Teammate {self.name} thread started, pty_file={self.pty_file}")
            tprint(f"Teammate {self.name} thread started, pty_file={self.pty_file}")
            
            # System prompt
            sys_prompt = (
                f"You are '{self.name}', role: {self.role}, team: {team_name}, at {self.config.workdir}. "
                f"Use idle tool when you have no more work."
            )
            
            # Initialize messages
            self.messages = [{"role": "user", "content": initial_prompt}]
            
            # Main loop
            while True:
                # -- WORK PHASE: standard agent loop --
                round_num = 0
                for _ in range(50):
                    round_num += 1
                    
                    # Check inbox
                    inbox = self.message_bus.read_inbox(self.name)
                    for msg in inbox:
                        if msg.type == MessageType.SHUTDOWN_REQUEST:
                            log_agent_event(self.name, 'shutdown', {'reason': 'shutdown_request'})
                            self._logger.info(f"Teammate {self.name} received shutdown request")
                            return
                        self.messages.append({"role": "user", "content": json.dumps(msg.to_dict())})
                    
                    # Call LLM
                    tprint("------------------------------------------------------------------------------------------------------------------------")
                    tprint(f"=== [teammate {self.name}] === {time.strftime('%Y-%m-%d %H:%M:%S')} round#{round_num} calling LLM ......")
                    
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
                            agent=self.name,
                            model=self.config.model_id,
                            input_tokens=response.usage.input_tokens,
                            output_tokens=response.usage.output_tokens,
                            duration_ms=duration_ms
                        )
                        
                    except Exception as e:
                        if hasattr(e, 'status_code'):
                            if e.status_code == 500:
                                self._logger.warning("LLM internal error, sleeping and retrying")
                                tprint("LLM internal error, sleep and retry")
                                time.sleep(30)
                                continue
                            elif e.status_code == 429:
                                self._logger.warning("Rate limit exceeded, sleeping and retrying")
                                tprint("RateLimitError, sleep and retry")
                                time.sleep(30)
                                continue
                        self._logger.error(f"Exception during LLM call: {e}")
                        tprint(f"Exception happened: {e}")
                        return
                    
                    # Update token stats
                    self.token_tracker.record(self.name, response, duration_ms)
                    
                    # Log response
                    tprint(f"=== [teammate {self.name}] === {time.strftime('%Y-%m-%d %H:%M:%S')} round#{round_num} LLM response: ")
                    if hasattr(response, "model_dump"):
                        tprint(json.dumps(response.model_dump(), indent=2, ensure_ascii=False))
                    
                    self.messages.append({"role": "assistant", "content": response.content})
                    
                    # Check if done
                    if response.stop_reason != "tool_use":
                        break
                    
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
                            results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": str(output),
                            })
                            
                            tprint("------------------------------------------------------------------------------------------------------------------------")
                            tprint(f"=== [teammate {self.name}] === {time.strftime('%Y-%m-%d %H:%M:%S')} round#{round_num} \"{block.name}\" result: ")
                            results_serialized = serialize_content(results)
                            tprint(json.dumps(results_serialized, indent=2, ensure_ascii=False))
                    
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
                                log_agent_event(self.name, 'shutdown', {'reason': 'shutdown_request'})
                                self._logger.info(f"Teammate {self.name} received shutdown request during idle")
                                return
                            self.messages.append({"role": "user", "content": json.dumps(msg.to_dict())})
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
                    return
                    
        finally:
            # Cleanup
            output_manager.set_target(None)
            if self.pty_file:
                self.pty_file.close()
    
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
        from libercode.tools.teammate_tools import create_teammate_tool_handlers
        
        handlers = create_teammate_tool_handlers(
            task_manager=self.task_manager,
            message_bus=self.message_bus,
            sender_name=self.name,
        )
        
        self._logger.debug(f"Executing tool: {tool_name}")
        handler = handlers.get(tool_name)
        return handler(**args) if handler else f"Unknown tool: {tool_name}"
    
    def _scan_unclaimed_tasks(self) -> List[Dict]:
        """Scan for unclaimed tasks."""
        from libercode.taskboard.models import TaskStatus
        
        all_tasks = []
        for f in sorted(self.task_manager.tasks_dir.glob("task_*.json")):
            task_data = json.loads(f.read_text())
            if (
                task_data.get("status") == "pending"
                and not task_data.get("owner")
                and not task_data.get("blockedBy")
            ):
                all_tasks.append(task_data)
        
        if all_tasks:
            self._logger.debug(f"Found {len(all_tasks)} unclaimed tasks")
        
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
        task_file.write_text(json.dumps(current_task, indent=2))
        
        # Log task claim
        log_task_event(task_id, 'claimed', {'teammate': self.name})
        self._logger.info(f"Claimed task #{task_id}: {task.get('subject', '')}")
        
        # Create task prompt
        task_prompt = (
            f"<auto-claimed>Task #{task['id']}: {task['subject']}\n"
            f"{task.get('description', '')}</auto-claimed>"
            f"When the task complete, send message to 'lead' with task-id and status to notify lead to update task status. Then use idle tool to back to idle state</auto-claimed>"
        )
        
        tprint("------------------------------------------------------------------------------------------------------------------------")
        tprint(f"=== [teammate {self.name}] === {time.strftime('%Y-%m-%d %H:%M:%S')} claimed task: {task_prompt} ")
        
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

"""
Lead agent for LiberCode.

Main orchestrator that manages tasks and teammates.
"""
import json
import time
import threading
from typing import List, Dict, Any
from dataclasses import dataclass, field
from anthropic import Anthropic
from pathlib import Path

from libercode.config import Config
from libercode.taskboard.manager import TaskManager
from libercode.messaging.bus import MessageBus
from libercode.messaging.serialization import serialize_content
from libercode.utils.token_tracker import TokenTracker
from libercode.ui.output import tprint
from libercode.exceptions import LLMInternalError, LLMRateLimitError


@dataclass
class LeadAgent:
    """
    Lead agent that orchestrates tasks and teammates.

    Attributes:
        client: Anthropic client
        config: Config instance
        message_bus: MessageBus instance
        task_manager: TaskManager instance
        teammate_manager: TeammateManager instance
        token_tracker: Token usage tracker
        messages: Conversation history
    """

    client: Anthropic
    config: Config
    message_bus: MessageBus
    task_manager: TaskManager
    teammate_manager: Any  # TeammateManager
    token_tracker: TokenTracker = field(default_factory=TokenTracker)
    messages: List[Dict] = field(default_factory=list)
    _input_counter: int = field(default=0, init=False)
    _agent_counter: int = field(default=0, init=False)

    def process_user_input(self, query: str) -> None:
        """
        Process user input through LLM loop.

        Args:
            query: User input string
        """
        # Add to message history
        self.messages.append({"role": "user", "content": query})

        # Increment counters
        self._input_counter += 1
        self._agent_counter = 0

        # Log input
        tprint("------------------------------------------------------------------------------------------------------------------------")
        tprint(f"<<<<<< [teammate lead] history input (round#{self._input_counter}) {time.strftime('%Y-%m-%d %H:%M:%S')} >>>>>>")
        history_serialized = serialize_content(self.messages)
        tprint(json.dumps(history_serialized, indent=2, ensure_ascii=False))

        # Run agent loop
        self._run_llm_loop()

        # Log output
        tprint("------------------------------------------------------------------------------------------------------------------------")
        tprint(f"<<<<<< [teammate lead] history output (round#{self._input_counter}) {time.strftime('%Y-%m-%d %H:%M:%S')} >>>>>>")
        history_serialized = serialize_content(self.messages)
        tprint(json.dumps(history_serialized, indent=2, ensure_ascii=False))
        tprint("------------------------------------------------------------------------------------------------------------------------")
        self.token_tracker.format_summary()
        tprint("------------------------------------------------------------------------------------------------------------------------")

    def _run_llm_loop(self) -> None:
        """
        Run LLM interaction loop with tool calling.
        """
        while True:
            self._agent_counter += 1

            # Check inbox before each LLM call
            inbox = self.message_bus.read_inbox("lead")
            if inbox:
                inbox_data = [msg.to_dict() for msg in inbox]
                self.messages.append({
                    "role": "user",
                    "content": f"<inbox>{json.dumps(inbox_data, indent=2)}</inbox>",
                })
                self.messages.append({
                    "role": "assistant",
                    "content": "Noted inbox messages.",
                })

            # Call LLM
            tprint("------------------------------------------------------------------------------------------------------------------------")
            tprint(f"=== [teammate lead] === {time.strftime('%Y-%m-%d %H:%M:%S')} user_input#{self._input_counter} round#{self._agent_counter} calling LLM ......")

            try:
                response = self.client.messages.create(
                    model=self.config.model_id,
                    system=self._get_system_prompt(),
                    messages=self.messages,
                    tools=self._get_tools(),
                    max_tokens=8000,
                )
            except Exception as e:
                # Handle rate limits and errors
                if hasattr(e, 'status_code'):
                    if e.status_code == 500:
                        tprint("LLM internal error, sleep and retry")
                        time.sleep(30)
                        continue
                    elif e.status_code == 429:
                        tprint("RateLimitError, sleep and retry")
                        time.sleep(30)
                        continue

                tprint(f"Exception happened: {e}")
                return

            # Update token stats
            self.token_tracker.update(response)

            # Log response
            tprint(f"=== [teammate lead] === {time.strftime('%Y-%m-%d %H:%M:%S')} user_input#{self._input_counter} round#{self._agent_counter} LLM response: ")
            if hasattr(response, "model_dump"):
                tprint(json.dumps(response.model_dump(), indent=2, ensure_ascii=False))

            # Add response to messages
            self.messages.append({"role": "assistant", "content": response.content})

            # Check if done
            if response.stop_reason != "tool_use":
                return

            # Execute tools
            results = []
            for block in response.content:
                if block.type == "tool_use":
                    handler = self._get_tool_handler(block.name)
                    try:
                        output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                    except Exception as e:
                        output = f"Error: {e}"

                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output),
                    })

                    # Log tool result
                    tprint("------------------------------------------------------------------------------------------------------------------------")
                    tprint(f"=== [teammate lead] === {time.strftime('%Y-%m-%d %H:%M:%S')} user_input#{self._input_counter} round#{self._agent_counter} user_run_tool \"{block.name}\" result: ")
                    results_serialized = serialize_content(results)
                    tprint(json.dumps(results_serialized, indent=2, ensure_ascii=False))

            self.messages.append({"role": "user", "content": results})

    def _get_system_prompt(self) -> str:
        """Get system prompt for lead agent"""
        return f"You are a team lead at {self.config.workdir}. When you get a task from user, firstly you should divide task to several sub tasks if need and meanwhile setup the dependence among subtasks. Base on sub tasks, spawn some teammates. The teammates are autonomous -- they find subtask themselves. Monitor all sub tasks and teammates. When need, send message to teammate."

    def _get_tools(self) -> List[Dict]:
        """Get lead agent tools"""
        from libercode.tools.lead_tools import get_lead_tools
        return get_lead_tools()

    def _get_tool_handler(self, tool_name: str):
        """Get handler for specific tool"""
        from libercode.tools.lead_tools import create_lead_tool_handlers

        # Create handlers on demand (could cache these)
        handlers = create_lead_tool_handlers(
            task_manager=self.task_manager,
            message_bus=self.message_bus,
            teammate_manager=self.teammate_manager,
        )

        return handlers.get(tool_name)

"""Lead agent for LiberCode.

Main orchestrator that manages tasks and teammates.
"""

import json
import time
import threading
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from anthropic import Anthropic
from pathlib import Path

from libercode.config import Config
from libercode.taskboard.manager import TaskManager
from libercode.messaging.bus import MessageBus
from libercode.messaging.serialization import serialize_content
from libercode.utils.token_tracker import TokenTracker
from libercode.utils.logging import get_logger, log_task_event, log_agent_event, log_llm_call
from libercode.ui.output import tprint, format_llm_response
from libercode.exceptions import LLMInternalError, LLMRateLimitError


@dataclass
class LeadAgent:
    """Lead agent that orchestrates tasks and teammates.
    
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
        # Add to message history
        self.messages.append({"role": "user", "content": query})
        
        # Increment counters
        self._input_counter += 1
        self._agent_counter = 0

        # Log input
        self._logger.info(f"Processing user input (round#{self._input_counter})")
        #self._logger.info(f"Input query: {query}")

        if self.config.debug:
            tprint("------------------------------------------------------------------------------------------------------------------------")
            tprint(f"<<<<<< [teammate lead] history input (round#{self._input_counter}) {time.strftime('%Y-%m-%d %H:%M:%S')} >>>>>>")
            history_serialized = serialize_content(self.messages)
            tprint(json.dumps(history_serialized, indent=2, ensure_ascii=False))

        # Run agent loop
        self._run_llm_loop()

        # Log output
        if self.config.debug:
            tprint("------------------------------------------------------------------------------------------------------------------------")
            tprint(f"<<<<<< [teammate lead] history output (round#{self._input_counter}) {time.strftime('%Y-%m-%d %H:%M:%S')} >>>>>>")
            history_serialized = serialize_content(self.messages)
            tprint(json.dumps(history_serialized, indent=2, ensure_ascii=False))
            tprint("------------------------------------------------------------------------------------------------------------------------")

        self._logger.info(f"Completed processing round#{self._input_counter}")
    
    def _run_llm_loop(self) -> None:
        """Run LLM interaction loop with tool calling."""
        while True:
            self._agent_counter += 1
            
            # Check inbox before each LLM call
            inbox = self.message_bus.read_inbox("lead")
            if inbox:
                self._logger.debug(f"Received {len(inbox)} inbox messages")
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
            if self.config.debug:
                tprint("------------------------------------------------------------------------------------------------------------------------")
                tprint(f"=== [teammate lead] === {time.strftime('%Y-%m-%d %H:%M:%S')} user_input#{self._input_counter} round#{self._agent_counter} calling LLM ......")
            tprint(f"lead system prompt: {self._get_system_prompt()}") 
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
                
                # Log LLM call
                log_llm_call(
                    agent='lead',
                    model=response.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    duration_ms=duration_ms
                )
                
            except Exception as e:
                # Handle rate limits and errors
                if hasattr(e, 'status_code'):
                    if e.status_code == 500 or e.status_code == 502:
                        self._logger.warning("LLM internal error, sleeping and retrying")
                        if self.config.debug:
                            tprint("LLM internal error, sleep and retry")
                        time.sleep(30)
                        continue
                    elif e.status_code == 429:
                        self._logger.warning("Rate limit exceeded, sleeping and retrying")
                        if self.config.debug:
                            tprint("RateLimitError, sleep and retry")
                        time.sleep(30)
                        continue
                self._logger.error(f"Exception during LLM call: {e}")
                tprint(f"Exception happened: {e}")
                return
            
            # Update token stats
            self.token_tracker.record("lead", response, duration_ms)

            # 记录 response 到 logger.debug
            if hasattr(response, "model_dump"):
                response_dict = response.model_dump()
                self._logger.info(f"LLM response: \n{json.dumps(response_dict, indent=2, ensure_ascii=False)}")
            else:
                self._logger.info(f"LLM response (raw): \n{response}")

            if self.config.debug:
                # print response to screen
                tprint(f"=== [teammate lead] === {time.strftime('%Y-%m-%d %H:%M:%S')} user_input#{self._input_counter} round#{self._agent_counter} LLM response: ")
                if hasattr(response, "model_dump"):
                    tprint(json.dumps(response.model_dump(), indent=2, ensure_ascii=False))

            # Add response to messages
            self.messages.append({"role": "assistant", "content": response.content})
            
            # Check if done
            if response.stop_reason != "tool_use":
                self._logger.debug("LLM loop completed without tool use")
                return
           
            if not self.config.debug:
                format_llm_response(response,"team lead")
                tprint()

            # Execute tools
            results = []
            for block in response.content:
                if block.type == "tool_use":
                    args_str = str(block.input)[:100] + ("..." if len(str(block.input)) > 100 else "")
                    tprint(f"Executing tool: {block.name}, args: {args_str}")
                    self._logger.info(f"Executing tool: {block.name}, args: {block.input}")
                    handler = self._get_tool_handler(block.name)
                    try:
                        output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                    except Exception as e:
                        self._logger.error(f"Tool {block.name} failed: {e}")
                        output = f"Error: {e}"
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output),
                    })
                    
                    if self.config.debug:
                        # Log tool result
                        tprint("------------------------------------------------------------------------------------------------------------------------")
                        tprint(f"=== [teammate lead] === {time.strftime('%Y-%m-%d %H:%M:%S')} user_input#{self._input_counter} round#{self._agent_counter} user_run_tool \"{block.name}\" result: ")
                        results_serialized = serialize_content(results)
                        json_dumps_str=json.dumps(results_serialized, indent=2, ensure_ascii=False)
                        tprint(json_dumps_str)
                        self._logger.debug(f"Executing tool: {block.name}, result: {json_dumps_str}")
                    # else:
                        # tprint(f"{str(output)}\n", color="yellow", style="italic")

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

    def _get_tool_handler(self, tool_name: str):
        """Get handler for specific tool."""
        from libercode.tools.lead_tools import create_lead_tool_handlers
        
        # Create handlers on demand (could cache these)
        handlers = create_lead_tool_handlers(
            task_manager=self.task_manager,
            message_bus=self.message_bus,
            teammate_manager=self.teammate_manager,
        )
        return handlers.get(tool_name)

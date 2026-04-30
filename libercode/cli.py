"""CLI entry point for LiberCode.

Provides REPL interface for user interaction.
"""

import asyncio
import json
import time
import sys
import signal
from pathlib import Path
from anthropic import Anthropic

from libercode.config import Config
from libercode.taskboard.manager import TaskManager
from libercode.messaging.bus import MessageBus
from libercode.messaging.serialization import serialize_content
from libercode.core.teammate_manager import TeammateManager
from libercode.core.lead import LeadAgent
from libercode.core.interrupt_handler import request_cancel, clear_cancel
from libercode.utils.logging import setup_logging, get_logger
from libercode.utils.token_tracker import TokenTracker
from libercode.ui.output import tprint

_current_task = None


def main():
    """Main CLI entry point.

    Initializes all components and runs REPL loop.
    """
    logger = setup_logging(
        log_dir=".libercode/logs",
        console_level="ERROR",
        file_level="DEBUG",
        use_colors=True,
        use_json=False,
    )
    log = get_logger('libercode.cli')
    log.info("Starting LiberCode CLI")

    try:
        config = Config()
        log.info(f"Configuration loaded: workdir={config.workdir}")
    except Exception as e:
        log.error(f"Configuration error: {e}")
        tprint(f"Configuration error: {e}")
        return 1

    client = Anthropic(api_key=config.api_key, base_url=config.base_url)
    log.info("Anthropic client initialized")

    message_bus = MessageBus(config.inbox_dir)
    task_manager = TaskManager(config.tasks_dir)
    log.info("MessageBus and TaskManager initialized")

    teammate_manager = TeammateManager(
        config=config,
        message_bus=message_bus,
        task_manager=task_manager,
        client=client,
        team_dir=config.team_dir,
    )

    lead = LeadAgent(
        client=client,
        async_client=config.async_client,
        config=config,
        message_bus=message_bus,
        task_manager=task_manager,
        teammate_manager=teammate_manager,
    )
    log.info("Lead agent initialized")

    tprint("LiberCode - AI Agent for Teams")
    tprint("Type 'q' or 'exit' to quit")
    tprint("Press Ctrl+C to interrupt LLM processing")
    tprint()

    try:
        asyncio.run(async_repl_loop(lead, message_bus, task_manager, teammate_manager, log))
    except KeyboardInterrupt:
        tprint("\nGoodbye!")
        log.info("LiberCode CLI shutting down")
        return 0

    log.info("LiberCode CLI shutting down")
    return 0


async def async_repl_loop(lead, message_bus, task_manager, teammate_manager, log):
    """Async REPL loop with interrupt support."""
    from libercode.ui.input_handler import input_with_cursor_support

    loop = asyncio.get_event_loop()
    interrupt_requested = False

    def handle_signal(signum, frame):
        nonlocal interrupt_requested
        interrupt_requested = True
        request_cancel()
        global _current_task
        if _current_task is not None and not _current_task.done():
            _current_task.cancel()

    previous_handler = signal.signal(signal.SIGINT, handle_signal)

    try:
        while True:
            try:
                query = await loop.run_in_executor(
                    None,
                    lambda: input_with_cursor_support("\033[36mlibercode >> \033[0m")
                )
            except (EOFError, KeyboardInterrupt):
                if interrupt_requested:
                    clear_cancel()
                    interrupt_requested = False
                    query = ""
                    continue
                tprint("\nGoodbye!")
                break

            if not query.strip():
                continue

            if query.strip().lower() in ("q", "exit"):
                log.info("User requested exit")
                tprint("Goodbye!")
                break

            if query.strip().startswith("/"):
                if query.strip() == "/help":
                    log.debug("Displaying help")
                    tprint("Available commands:")
                    tprint(" /help - Show this help message")
                    tprint(" /team - List all team members")
                    tprint(" /inbox - Check lead's inbox messages")
                    tprint(" /tokens - Show token usage statistics")
                    tprint(" /tasks - List all tasks")
                    tprint(" /init - Initialize/update AGENTS.md")
                    tprint(" /review - Review the project")
                    tprint(" /clear - Clear lead's message history")
                    tprint(" /clear <teammate> - Clear specific teammate's message history")
                    tprint(" /clear all - Clear lead and all teammates' message history")
                    tprint(" q, exit - Exit the application")
                    continue

                if query.strip() == "/team":
                    log.debug("Listing team members")
                    tprint(teammate_manager.list_all())
                    continue

                if query.strip() == "/inbox":
                    log.debug("Checking inbox")
                    messages = message_bus.read_inbox("lead")
                    if not messages:
                        tprint("No messages in inbox.")
                    else:
                        for msg in messages:
                            tprint(f"From {msg.sender}: {msg.content}")
                    continue

                if query.strip().startswith("/tokens"):
                    log.debug("Token tracking requested")
                    parts = query.strip().split()
                    args = parts[1:] if len(parts) > 1 else []
                    tracker = TokenTracker.get_tracker()
                    tprint(tracker.output(args))
                    continue

                if query.strip() == "/tasks":
                    log.debug("Listing tasks")
                    tprint(task_manager.list_all())
                    continue

                if query.strip() == "/init":
                    log.debug("Running /init command to create/update AGENTS.md")
                    prompt_path = Path(__file__).parent / "prompts" / "init_agents_md.txt"
                    init_prompt = prompt_path.read_text()
                    await run_llm_with_interrupt(lead, init_prompt, log)
                    continue

                if query.strip() == "/review":
                    log.debug("Running /review command to review the project")
                    prompt_path = Path(__file__).parent / "prompts" / "review.txt"
                    init_prompt = prompt_path.read_text()
                    await run_llm_with_interrupt(lead, init_prompt, log)
                    continue

                if query.strip().startswith("/clear"):
                    log.debug("Clearing message history")
                    parts = query.strip().split()
                    args = parts[1:] if len(parts) > 1 else []

                    if not args:
                        lead.clear_messages()
                        tprint("Lead message history cleared.")
                    elif args[0] == "all":
                        lead.clear_messages()
                        for name in teammate_manager.member_names():
                            teammate = teammate_manager.get_teammate(name)
                            if teammate:
                                teammate.clear_messages()
                        tprint("All message histories cleared (lead and all teammates).")
                    else:
                        teammate_name = args[0]
                        teammate = teammate_manager.get_teammate(teammate_name)
                        if teammate:
                            teammate.clear_messages()
                            tprint(f"Teammate '{teammate_name}' message history cleared.")
                        else:
                            tprint(f"Error: Teammate '{teammate_name}' not found.")
                    continue

                tprint("Error: No matching command")
                continue

            log.info(f"Processing user input: \n{query}")
            await run_llm_with_interrupt(lead, query, log)
    finally:
        signal.signal(signal.SIGINT, previous_handler)


async def run_llm_with_interrupt(lead, query, log):
    """Run LLM processing with interrupt checking."""
    clear_cancel()
    lead._inject_agents_md()
    lead.messages.append({"role": "user", "content": query})
    lead._input_counter += 1
    lead._agent_counter = 0

    log.info(f"Processing user input (round#{lead._input_counter})")

    if lead.config.debug:
        history_serialized = serialize_content(lead.messages)
        log.debug(f"[lead] history input (round#{lead._input_counter}):\n{json.dumps(history_serialized, indent=2, ensure_ascii=False)}")

    task = asyncio.create_task(lead.async_run_llm_loop())
    global _current_task
    _current_task = task

    while not task.done():
        await asyncio.sleep(0.1)

    try:
        await task
    except asyncio.CancelledError:
        tprint("\n[Interrupted by user during await task]")
        return
    finally:
        _current_task = None

    if lead.config.debug:
        tprint(f"<<<<<< [teammate lead] history output (round#{lead._input_counter}) {time.strftime('%Y-%m-%d %H:%M:%S')} >>>>>>")
        history_serialized = serialize_content(lead.messages)
        log.debug(f"[lead] history output (round#{lead._input_counter}):\n{json.dumps(history_serialized, indent=2, ensure_ascii=False)}")

    log.info(f"Completed processing round#{lead._input_counter}")

    if lead.messages:
        last_message = lead.messages[-1]
        if last_message.get("role") == "assistant":
            content = last_message.get("content")
            if isinstance(content, list):
                for block in content:
                    if block.get("text"):
                        tprint(block["text"], color="cyan", style="bold")
            elif isinstance(content, str):
                tprint(content, color="cyan", style="bold")


if __name__ == "__main__":
    sys.exit(main())

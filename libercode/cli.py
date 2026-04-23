"""CLI entry point for LiberCode.

Provides REPL interface for user interaction.
"""

import sys
from pathlib import Path
from anthropic import Anthropic

from libercode.config import Config
from libercode.taskboard.manager import TaskManager
from libercode.messaging.bus import MessageBus
from libercode.core.teammate_manager import TeammateManager
from libercode.core.lead import LeadAgent
from libercode.utils.logging import setup_logging, get_logger
from libercode.utils.token_tracker import TokenTracker
from libercode.ui.output import tprint
from libercode.ui.input_handler import input_with_cursor_support


def main():
    """Main CLI entry point.
    
    Initializes all components and runs REPL loop.
    """
    # Setup logging system
    logger = setup_logging(
        log_dir=".libercode/logs",
        console_level="ERROR",
        file_level="DEBUG",
        use_colors=True,
        use_json=False,
    )
    log = get_logger('libercode.cli')
    log.info("Starting LiberCode CLI")
    
    # Load configuration
    try:
        config = Config()
        log.info(f"Configuration loaded: workdir={config.workdir}")
    except Exception as e:
        log.error(f"Configuration error: {e}")
        tprint(f"Configuration error: {e}")
        return 1
    
    # Initialize Anthropic client
    client = Anthropic(api_key=config.api_key, base_url=config.base_url)
    log.info("Anthropic client initialized")
    
    # Initialize components
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
    
    # Create lead agent
    lead = LeadAgent(
        client=client,
        config=config,
        message_bus=message_bus,
        task_manager=task_manager,
        teammate_manager=teammate_manager,
    )
    log.info("Lead agent initialized")
    
    # Welcome message
    tprint("LiberCode - AI Agent for Teams")
    tprint("Type 'q' or 'exit' to quit")
    tprint()
    
    # REPL loop
    while True:
        try:
            query = input_with_cursor_support("\033[36mlibercode >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            tprint("\nGoodbye!")
            break

        # Handle empty input - continue loop
        if not query.strip():
            continue

        # Handle exit
        if query.strip().lower() in ("q", "exit"):
            log.info("User requested exit")
            tprint("Goodbye!")
            break
        if query.strip().startswith("/"):
            # Handle commands
            if query.strip() == "/help":
                log.debug("Displaying help")
                tprint("Available commands:")
                tprint("  /help              - Show this help message")
                tprint("  /team              - List all team members")
                tprint("  /inbox             - Check lead's inbox messages")
                tprint("  /tokens            - Show token usage statistics")
                tprint("  /tasks             - List all tasks")
                tprint("  /init              - Initialize/update AGENTS.md")
                tprint("  /review            - Review the project")
                tprint("  /clear             - Clear lead's message history")
                tprint("  /clear <teammate>  - Clear specific teammate's message history")
                tprint("  /clear all         - Clear lead and all teammates' message history")
                tprint("  q, exit            - Exit the application")
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
                lead.process_user_input(init_prompt)
                if lead.messages:
                    last_message = lead.messages[-1]
                    if isinstance(last_message.get("content"), list):
                        for block in last_message["content"]:
                            if hasattr(block, "text"):
                                tprint(block.text)
                tprint()
                continue

            if query.strip() == "/review":
                log.debug("Running /review command to review the project")
                prompt_path = Path(__file__).parent / "prompts" / "review.txt"
                init_prompt = prompt_path.read_text()
                lead.process_user_input(init_prompt)
                if lead.messages:
                    last_message = lead.messages[-1]
                    if isinstance(last_message.get("content"), list):
                        for block in last_message["content"]:
                            if hasattr(block, "text"):
                                tprint(block.text)
                tprint()
                continue

            # Handle /clear command
            if query.strip().startswith("/clear"):
                log.debug("Clearing message history")
                parts = query.strip().split()
                args = parts[1:] if len(parts) > 1 else []

                if not args:
                    # Clear lead's messages only
                    lead.clear_messages()
                    tprint("Lead message history cleared.")
                elif args[0] == "all":
                    # Clear lead and all teammates
                    lead.clear_messages()
                    for name in teammate_manager.member_names():
                        # Find the teammate thread and clear its messages
                        teammate = teammate_manager.get_teammate(name)
                        if teammate:
                            teammate.clear_messages()
                    tprint("All message histories cleared (lead and all teammates).")
                else:
                    # Clear specific teammate's messages
                    teammate_name = args[0]
                    teammate = teammate_manager.get_teammate(teammate_name)
                    if teammate:
                        teammate.clear_messages()
                        tprint(f"Teammate '{teammate_name}' message history cleared.")
                    else:
                        tprint(f"Error: Teammate '{teammate_name}' not found.")
                continue

            tprint(f"Error: No matching command")
            continue


        # Process user input
        log.info(f"Processing user input: \n{query}")
        lead.process_user_input(query)
        
        # Print response
        if lead.messages:
            last_message = lead.messages[-1]
            if isinstance(last_message.get("content"), list):
                for block in last_message["content"]:
                    if hasattr(block, "text"):
                        tprint(block.text, color="blue", style="bold")
        tprint()
    
    log.info("LiberCode CLI shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())

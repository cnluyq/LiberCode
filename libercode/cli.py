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
        log.debug(f"Configuration loaded: workdir={config.workdir}")
    except Exception as e:
        log.error(f"Configuration error: {e}")
        print(f"Configuration error: {e}")
        return 1
    
    # Initialize Anthropic client
    client = Anthropic(base_url=config.base_url)
    log.debug("Anthropic client initialized")
    
    # Initialize components
    message_bus = MessageBus(config.inbox_dir)
    task_manager = TaskManager(config.tasks_dir)
    log.debug("MessageBus and TaskManager initialized")
    
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
    print("LiberCode - AI Agent for Teams")
    print("Type 'q' or 'exit' to quit")
    print()
    
    # REPL loop
    while True:
        try:
            query = input("\033[36mlibercode >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        # Handle empty input - continue loop
        if not query.strip():
            continue

        # Handle exit
        if query.strip().lower() in ("q", "exit"):
            log.info("User requested exit")
            print("Goodbye!")
            break
        if query.strip().startswith("/"):
            # Handle commands
            if query.strip() == "/help":
                log.debug("Displaying help")
                print("Available commands:")
                print("  /help              - Show this help message")
                print("  /team              - List all team members")
                print("  /inbox             - Check lead's inbox messages")
                print("  /tokens            - Show token usage statistics")
                print("  /tasks             - List all tasks")
                print("  /init              - Initialize/update AGENTS.md")
                print("  /review            - Review the project")
                print("  /clear             - Clear lead's message history")
                print("  /clear <teammate>  - Clear specific teammate's message history")
                print("  /clear all         - Clear lead and all teammates' message history")
                print("  q, exit            - Exit the application")
                continue

            if query.strip() == "/team":
                log.debug("Listing team members")
                print(teammate_manager.list_all())
                continue

            if query.strip() == "/inbox":
                log.debug("Checking inbox")
                messages = message_bus.read_inbox("lead")
                for msg in messages:
                    print(f"From {msg.sender}: {msg.content}")
                continue

            if query.strip().startswith("/tokens"):
                log.debug("Token tracking requested")
                parts = query.strip().split()
                args = parts[1:] if len(parts) > 1 else []
                tracker = TokenTracker.get_tracker()
                print(tracker.output(args))
                continue

            if query.strip() == "/tasks":
                log.debug("Listing tasks")
                print(task_manager.list_all())
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
                                print(block.text)
                print()
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
                                print(block.text)
                print()
                continue

            # Handle /clear command
            if query.strip().startswith("/clear"):
                log.debug("Clearing message history")
                parts = query.strip().split()
                args = parts[1:] if len(parts) > 1 else []

                if not args:
                    # Clear lead's messages only
                    lead.clear_messages()
                    print("Lead message history cleared.")
                elif args[0] == "all":
                    # Clear lead and all teammates
                    lead.clear_messages()
                    for name in teammate_manager.member_names():
                        # Find the teammate thread and clear its messages
                        teammate = teammate_manager.get_teammate(name)
                        if teammate:
                            teammate.clear_messages()
                    print("All message histories cleared (lead and all teammates).")
                else:
                    # Clear specific teammate's messages
                    teammate_name = args[0]
                    teammate = teammate_manager.get_teammate(teammate_name)
                    if teammate:
                        teammate.clear_messages()
                        print(f"Teammate '{teammate_name}' message history cleared.")
                    else:
                        print(f"Error: Teammate '{teammate_name}' not found.")
                continue

            print(f"Error: No matching command")
            continue


        # Process user input
        log.debug(f"Processing user input: {query[:50]}...")
        lead.process_user_input(query)
        
        # Print response
        if lead.messages:
            last_message = lead.messages[-1]
            if isinstance(last_message.get("content"), list):
                for block in last_message["content"]:
                    if hasattr(block, "text"):
                        print(block.text)
        print()
    
    log.info("LiberCode CLI shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())

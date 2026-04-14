"""
CLI entry point for LiberCode.

Provides REPL interface for user interaction.
"""
import sys
from pathlib import Path
from anthropic import Anthropic

from libercode.config import Config
from libercode.taskboard.manager import TaskManager
from libercode.messaging.bus import MessageBus
from libercode.teammate_manager import TeammateManager
from libercode.core.lead import LeadAgent


def main():
    """
    Main CLI entry point.

    Initializes all components and runs REPL loop.
    """
    # Load configuration
    try:
        config = Config()
    except Exception as e:
        print(f"Configuration error: {e}")
        return 1

    # Initialize Anthropic client
    client = Anthropic(base_url=config.base_url)

    # Initialize components
    message_bus = MessageBus(config.inbox_dir)
    task_manager = TaskManager(config.tasks_dir)
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

        # Handle exit
        if query.strip().lower() in ("q", "exit", ""):
            print("Goodbye!")
            break

        # Handle commands
        if query.strip() == "/team":
            print(teammate_manager.list_all())
            continue

        if query.strip() == "/inbox":
            messages = message_bus.read_inbox("lead")
            for msg in messages:
                print(f"From {msg.sender}: {msg.content}")
            continue

        if query.strip() == "/tokens":
            print("Token tracking not yet integrated")
            continue

        if query.strip() == "/tasks":
            print(task_manager.list_all())
            continue

        # Process user input
        lead.process_user_input(query)

        # Print response
        if lead.messages:
            last_message = lead.messages[-1]
            if isinstance(last_message.get("content"), list):
                for block in last_message["content"]:
                    if hasattr(block, "text"):
                        print(block.text)
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())

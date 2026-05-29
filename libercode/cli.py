"""CLI entry point for LiberCode.

Provides REPL interface for user interaction.
"""
import os
import subprocess
import asyncio
import json
import shutil
import time
import sys
import signal
import uuid
from datetime import datetime
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
from libercode.ui.status_pane import StatusPane
from libercode.ui import is_tmux_available
from libercode.session_manager import SessionManager, AutoSaver, SessionRecoveryManager, SessionMeta

_current_task = None


def _cleanup_workdir_dirs(config):
    for d in (config.team_dir, config.tasks_dir):
        try:
            if d.exists():
                shutil.rmtree(d)
        except Exception:
            pass


def _generate_session_name() -> str:
    """Generate unique session name with timestamp and random suffix."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = uuid.uuid4().hex[:6]
    return f"session_{timestamp}_{random_suffix}"


def main():
    """Main CLI entry point.

    Initializes all components and runs REPL loop.
    """
    session_name = _generate_session_name()
    logger = setup_logging(
        session_name=session_name,
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

    session_manager = SessionManager(
        session_dir=config.workdir,
        lead=lead,
        teammate_manager=teammate_manager,
        task_manager=task_manager,
        message_bus=message_bus,
    )
    session_manager.create_session(session_name)
    auto_saver = AutoSaver(session_manager, initial_interval=config.session_auto_save_interval)
    log.info("Session manager initialized")

    status_pane = None
    status_refresh = float(os.getenv("LIBERCODE_STATUS_REFRESH", "5.0"))
    if status_refresh > 0 and is_tmux_available():
        status_pane = StatusPane(
            task_manager=task_manager,
            teammate_manager=teammate_manager,
            lead=lead,
            session_manager=session_manager,
            refresh_interval=status_refresh,
        )
        status_pane.start()
        log.info(f"Status pane started (refresh={status_refresh}s)")

    tprint("LiberCode - AI Agent for Teams")
    tprint("Type 'q' or 'exit' to quit")
    tprint("Press Ctrl+C to interrupt LLM processing")
    tprint()

    try:
        if config.session_auto_save:
            asyncio.run(async_repl_loop(lead, message_bus, task_manager, teammate_manager, log, auto_saver, session_manager, status_pane, config))
        else:
            asyncio.run(async_repl_loop(lead, message_bus, task_manager, teammate_manager, log, None, None, status_pane, config))
    except KeyboardInterrupt:
        teammate_manager.close_all_teammates(status_pane=status_pane)
        tprint("\nGoodbye!")
        log.info("LiberCode CLI shutting down")
        _cleanup_workdir_dirs(config)
        return 0

    log.info("LiberCode CLI shutting down")
    return 0


async def async_repl_loop(lead, message_bus, task_manager, teammate_manager, log, auto_saver=None, session_manager=None, status_pane=None, config=None):
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

    if auto_saver:
        auto_saver.start()

    try:
        while True:
            try:
                query = await loop.run_in_executor(
                    None,
                    lambda: input_with_cursor_support()
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
                tprint("Cleaning ... ...")
                teammate_manager.close_all_teammates(status_pane=status_pane)
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
                    tprint(" /sessions - List all saved sessions")
                    tprint(" /sessions resubject - Rewrite subject of current session")
                    tprint(" /sessions restore <name_or_number> - Restore a session")
                    tprint(" /sessions delete <name_or_number> - Delete a session")
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
                    message = {"role": "user", "content": init_prompt}
                    await run_llm_with_interrupt(lead, message, log)
                    continue

                if query.strip() == "/review":
                    log.debug("Running /review command to review the project")
                    prompt_path = Path(__file__).parent / "prompts" / "review.txt"
                    review_prompt = prompt_path.read_text()
                    message = {"role": "user", "content": review_prompt}
                    await run_llm_with_interrupt(lead, message, log)
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

                if query.strip().startswith("/sessions"):
                    parts = query.strip().split()
                    sub_cmd = parts[1] if len(parts) > 1 else None

                    if not session_manager or not auto_saver:
                        tprint("Session management is not enabled.")
                        continue

                    current_session_name = session_manager.get_current_session_name()
                    recovery_manager = SessionRecoveryManager(Path.cwd())

                    if sub_cmd == None:
                        sessions = recovery_manager.list_sessions()
                        if not sessions:
                            tprint("No saved sessions found.")
                        else:
                            tprint("Available sessions:")
                            for i, sess in enumerate(sessions, 1):
                                created = sess.get("created_at", "unknown")
                                updated = sess.get("updated_at", "unknown")
                                session_name = sess.get("session_name", "unknown")
                                subject = sess.get("subject", "")
                                save_count = sess.get("save_count", 0)
                                if session_name == current_session_name:
                                    tprint(f" {i}. {subject} (current)")
                                else:
                                    tprint(f" {i}. {subject}")

                                tprint(f"    Created: {created}  Updated: {updated}  Saves: {save_count}")
                        continue

                    if sub_cmd != "restore" and sub_cmd != "delete" and sub_cmd != "resubject":
                        tprint(f"Unavailable sub-command: {sub_cmd}")
                        tprint("Available Sessions sub-commands:")
                        tprint("          /sessions resubject")
                        tprint("          /sessions restore <name_or_number>")
                        tprint("          /sessions delete <name_or_number>")
                        continue

                    if sub_cmd == "resubject":
                        current_subject = session_manager.get_current_subject() or "(empty)"
                        tprint(f"Current subject: {current_subject}")
                        try:
                            new_subject = await loop.run_in_executor(
                                None, lambda: input("New subject: ")
                            )
                            new_subject = new_subject.strip()
                        except (EOFError, KeyboardInterrupt):
                            tprint("\nResubject cancelled.")
                            continue
                        if not new_subject:
                            tprint("Subject cannot be empty.")
                            continue
                        if len(new_subject) > 100:
                            tprint(f"Subject too long: {len(new_subject)} chars (max 100).")
                            continue
                        session_manager.update_subject(new_subject)
                        tprint(f"Subject updated: {new_subject[:100]}")
                        continue

                    if sub_cmd == "restore" and len(parts) > 2:
                        session_identifier = " ".join(parts[2:])
                    elif sub_cmd == "delete" and len(parts) > 2:
                        session_identifier = " ".join(parts[2:])
                    elif sub_cmd == "restore" or sub_cmd == "delete":
                        tprint(f"Usage: /sessions {sub_cmd} <name_or_number>")
                        continue

                    sessions = recovery_manager.list_sessions()
                    session_name = None

                    try:
                        idx = int(session_identifier) - 1
                        if 0 <= idx < len(sessions):
                            session_name = sessions[idx].get("session_name")
                        else:
                            tprint(f"Invalid session number: {session_identifier}")
                            continue
                    except ValueError:
                        session_name = session_identifier

                    if not session_name:
                        tprint(f"Session not found: {session_identifier}")
                        continue

                    if session_name == current_session_name:
                        tprint(f"Cannot {sub_cmd} the current active session: {session_name}")
                        continue

                    if sub_cmd == "delete":
                        if recovery_manager.delete_session(session_name):
                            tprint(f"Deleted session: {session_name}")
                        else:
                            tprint(f"Session not found: {session_name}")
                        continue

                    if sub_cmd == "restore":
                        log.info(f"Restoring session: {session_name}")
                        tprint(f"Restoring session: {session_name}")

                        await auto_saver.stop()

                        try:
                            summary = recovery_manager.restore_session(
                                session_name=session_name,
                                lead=lead,
                                teammate_manager=teammate_manager,
                                task_manager=task_manager,
                                message_bus=message_bus,
                            )

                            if "error" in summary:
                                tprint(f"Restore failed: {summary['error']}")
                            else:
                                session_manager._current_session = None
                                session_path = session_manager._get_session_path(session_name)
                                if session_path.exists():
                                    meta_path = session_path / "meta.json"
                                    meta_data = json.loads(meta_path.read_text()) if meta_path.exists() else {}
                                    session_manager._current_session = SessionMeta(
                                        session_id=meta_data.get("session_id", ""),
                                        session_name=session_name,
                                        subject=meta_data.get("subject", ""),
                                        created_at=meta_data.get("created_at", ""),
                                        updated_at=datetime.now().isoformat(),
                                        save_count=0,
                                        interval_seconds=1.0,
                                    )
                                else:
                                    session_manager.create_session(session_name)

                                session_manager._file_mtimes.clear()

                                restored = summary.get("restored", {})
                                tprint(f"Session restored: {session_name}")
                                if "tasks" in restored:
                                    tprint(f"  Tasks: {restored['tasks']} restored")
                                if "lead_messages" in restored:
                                    tprint(f"  Lead messages: {restored['lead_messages']} restored")
                                if "lead_inbox" in restored:
                                    tprint("  Lead inbox: restored")
                                if "teammates" in restored:
                                    for tm in restored["teammates"]:
                                        tprint(f"  Teammate '{tm['name']}': {tm['messages']} messages restored")
                                if "teammate_inboxes" in restored:
                                    tprint("  Teammate inboxes: restored")
                                if "team_config" in restored:
                                    tprint("  Team config: restored")
                                if "token_records" in restored:
                                    tprint(f"  Token records: {restored['token_records']} restored")
                        except Exception as e:
                            log.error(f"Restore failed: {e}")
                            tprint(f"Restore failed: {e}")
                        finally:
                            auto_saver.start()

                        continue


                tprint(f"Error: no such command. run /help for usage.")
                continue

            if query.strip().startswith("!"):
                cmd = query[1:].strip()
                if not cmd:
                    tprint("! no command input")
                    continue
                try:
                    result = subprocess.run(
                        cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    if result.stdout:
                        tprint(result.stdout.rstrip())
                    if result.stderr:
                        tprint(f"[stderr]\n{result.stderr.rstrip()}")
                    if result.returncode != 0:
                        tprint(f"command return code: {result.returncode}")
                except subprocess.TimeoutExpired:
                    tprint("command timeout")
                except Exception as e:
                    tprint(f"command error: {e}")
                continue 

            log.info(f"Processing user input: \n{query}")
            if session_manager:
                session_manager.set_initial_subject(query)
            message = {"role": "user", "content": query}
            await run_llm_with_interrupt(lead, message, log)
    finally:
        if auto_saver:
            await auto_saver.stop()
        teammate_manager.close_all_teammates(status_pane=status_pane)
        if config:
            _cleanup_workdir_dirs(config)
        signal.signal(signal.SIGINT, previous_handler)


async def run_llm_with_interrupt(lead, message, log):
    """Run LLM processing with interrupt checking and user input request support."""
    clear_cancel()
    lead._inject_agents_md()
    lead.messages.append(message)
    lead._input_counter += 1
    lead._agent_counter = 0

    log.info(f"Processing user input (round#{lead._input_counter})")

    if lead.config.debug:
        history_serialized = serialize_content(lead.messages)
        log.debug(f"[lead] history input (round#{lead._input_counter}):\n{json.dumps(history_serialized, indent=2, ensure_ascii=False)}")

    task = asyncio.create_task(lead.async_run_llm_loop())
    global _current_task
    _current_task = task

    loop = asyncio.get_event_loop()
    from libercode.ui.input_handler import input_with_cursor_support

    while not task.done():
        if lead._user_input_request_id is not None:
            try:
                user_response = await loop.run_in_executor(
                    None,
                    lambda: input_with_cursor_support()
                )
            except (EOFError, KeyboardInterrupt):
                user_response = "skip"
            lead.provide_user_input(user_response if user_response.strip() else "skip")
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
        if isinstance(last_message.get("content"), list):
            for block in last_message["content"]:
                if hasattr(block, "text"):
                    text = block.text.strip()
                    if text:
                        tprint(text, color="cyan", style="bold")


if __name__ == "__main__":
    sys.exit(main())

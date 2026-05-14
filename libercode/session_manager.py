"""
Session persistence manager for LiberCode.

Provides automatic periodic saving of session state and recovery functionality.
"""

import asyncio
import json
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
import copy

from libercode.utils.logging import get_logger
from libercode.messaging.serialization import serialize_content


@dataclass
class SessionMeta:
    """Session metadata."""
    session_id: str
    session_name: str
    created_at: str
    updated_at: str
    save_count: int = 0
    interval_seconds: float = 1.0


class SessionManager:
    """
    Manages session persistence and recovery.

    Stores:
    - lead messages
    - team config
    - tasks
    - teammate messages (inbox + agent messages)
    """

    def __init__(
        self,
        session_dir: Path,
        lead=None,
        teammate_manager=None,
        task_manager=None,
        message_bus=None,
    ):
        self.session_dir = Path(session_dir) / ".libercode" / "sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.lead = lead
        self.teammate_manager = teammate_manager
        self.task_manager = task_manager
        self.message_bus = message_bus

        self._lock = threading.RLock()
        self._current_session: Optional[SessionMeta] = None
        self._save_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="session_save")

        self._file_mtimes: Dict[str, float] = {}

        self._save_count = 0
        self._min_interval = 1.0
        self._current_interval = 1.0
        self._last_save_duration = 0.0
        self._consecutive_slow_saves = 0

        self._logger = get_logger('libercode.session')

    def _generate_session_name(self) -> str:
        """Generate unique session name with timestamp and random suffix."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = uuid.uuid4().hex[:6]
        return f"session_{timestamp}_{random_suffix}"

    def _get_session_path(self, session_name: str) -> Path:
        """Get path for session directory."""
        return self.session_dir / session_name

    def create_session(self) -> str:
        """Create a new session directory and return session name."""
        with self._lock:
            session_name = self._generate_session_name()
            session_path = self._get_session_path(session_name)

            session_path.mkdir(parents=True, exist_ok=True)
            (session_path / "tasks").mkdir(exist_ok=True)
            (session_path / "teammates").mkdir(exist_ok=True)

            self._current_session = SessionMeta(
                session_id=uuid.uuid4().hex,
                session_name=session_name,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
                save_count=0,
                interval_seconds=self._current_interval,
            )
            self._save_meta()

            self._logger.info(f"Created new session: {session_name}")
            return session_name

    def _save_meta(self) -> None:
        """Save session metadata."""
        if not self._current_session:
            return

        meta_path = self._get_session_path(self._current_session.session_name) / "meta.json"
        meta_data = {
            "session_id": self._current_session.session_id,
            "session_name": self._current_session.session_name,
            "created_at": self._current_session.created_at,
            "updated_at": self._current_session.updated_at,
            "save_count": self._current_session.save_count,
            "interval_seconds": self._current_session.interval_seconds,
        }
        meta_path.write_text(json.dumps(meta_data, indent=2))

    def _copy_file_if_changed(self, src: Path, dst: Path) -> bool:
        """Copy file only if source has changed (based on mtime)."""
        if not src.exists():
            return False

        key = str(src)
        src_mtime = src.stat().st_mtime

        if self._file_mtimes.get(key) == src_mtime:
            return False

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        self._file_mtimes[key] = src_mtime
        return True

    def _save_lead_messages(self, session_path: Path) -> bool:
        """Save lead messages to file."""
        if not self.lead:
            return False

        with self._lock:
            messages = serialize_content(self.lead.messages)

        lead_path = session_path / "lead.json"
        data = {"messages": messages, "saved_at": datetime.now().isoformat()}
        lead_path.write_text(json.dumps(data, indent=2))
        self._file_mtimes[str(lead_path)] = time.time()
        return True

    def _save_team_config(self, session_path: Path) -> bool:
        """Save team config to file."""
        if not self.teammate_manager:
            return False

        config_path = getattr(self.teammate_manager, 'config_path', None)
        if not config_path or not config_path.exists():
            return False

        dst = session_path / "team_config.json"
        return self._copy_file_if_changed(config_path, dst)

    def _save_tasks(self, session_path: Path) -> bool:
        """Save tasks directory to file."""
        if not self.task_manager:
            return False

        tasks_src = self.task_manager.tasks_dir
        tasks_dst = session_path / "tasks"

        changed = False
        if tasks_src.exists():
            for src_file in tasks_src.glob("task_*.json"):
                if self._copy_file_if_changed(src_file, tasks_dst / src_file.name):
                    changed = True

        return changed

    def _save_lead_inbox(self, session_path: Path) -> bool:
        """Save lead's inbox from message bus."""
        if not self.message_bus:
            return False

        inbox_src = self.message_bus.inbox_dir / "lead.jsonl"
        inbox_dst = session_path / "lead_inbox.jsonl"
        return self._copy_file_if_changed(inbox_src, inbox_dst)

    def _save_teammate_messages(self, session_path: Path) -> bool:
        """Save teammate messages (from message bus inbox and agent state)."""
        if not self.teammate_manager or not self.message_bus:
            return False

        changed = False
        teammates_dst = session_path / "teammates"

        for name in self.teammate_manager.member_names():
            inbox_src = self.message_bus.inbox_dir / f"{name}.jsonl"
            inbox_dst = teammates_dst / f"{name}_inbox.jsonl"

            if self._copy_file_if_changed(inbox_src, inbox_dst):
                changed = True

            teammate = self.teammate_manager.get_teammate(name)
            if teammate:
                agent_dst = teammates_dst / f"{name}_messages.json"
                with self._lock:
                    try:
                        messages = serialize_content(teammate.messages)
                        data = {"messages": messages, "saved_at": datetime.now().isoformat()}
                        agent_dst.write_text(json.dumps(data, indent=2))
                        self._file_mtimes[str(agent_dst)] = time.time()
                        changed = True
                    except Exception as e:
                        self._logger.warning(f"Failed to save messages for {name}: {e}")

        return changed

    def _do_save(self) -> tuple[bool, float]:
        """Perform a save operation. Returns (changed, duration)."""
        if not self._current_session:
            return False, 0.0

        session_path = self._get_session_path(self._current_session.session_name)
        start_time = time.time()

        changed = False
        changed = self._save_lead_messages(session_path) or changed
        changed = self._save_lead_inbox(session_path) or changed
        changed = self._save_team_config(session_path) or changed
        changed = self._save_tasks(session_path) or changed
        changed = self._save_teammate_messages(session_path) or changed

        if changed:
            self._current_session.save_count += 1
            self._current_session.updated_at = datetime.now().isoformat()
            self._save_meta()

        duration = time.time() - start_time
        return changed, duration

    def save(self) -> bool:
        """Thread-safe save operation."""
        changed, duration = self._do_save()

        with self._lock:
            self._last_save_duration = duration
            self._save_count += 1

            if duration > self._current_interval * 0.8:
                self._consecutive_slow_saves += 1
                if self._consecutive_slow_saves >= 3:
                    new_interval = min(self._current_interval * 1.5, 10.0)
                    if new_interval > self._current_interval:
                        self._current_interval = new_interval
                        self._logger.warning(
                            f"Auto-save taking too long ({duration:.2f}s). "
                            f"Increasing interval to {self._current_interval:.1f}s"
                        )
                    self._consecutive_slow_saves = 0
            else:
                self._consecutive_slow_saves = 0

                if self._current_interval > self._min_interval:
                    new_interval = max(self._current_interval * 0.9, self._min_interval)
                    if new_interval < self._current_interval:
                        self._current_interval = new_interval
                        self._logger.debug(f"Decreasing auto-save interval to {self._current_interval:.1f}s")

        return changed

    def save_async(self) -> None:
        """Non-blocking async save."""
        self._save_pool.submit(self.save)

    def get_interval(self) -> float:
        """Get current save interval."""
        return self._current_interval

    def get_current_session_name(self) -> Optional[str]:
        """Get current session name."""
        return self._current_session.session_name if self._current_session else None


class AutoSaver:
    """
    Automatic periodic session saver with adaptive interval.

    Runs in background, periodically calling SessionManager.save().
    Adjusts interval based on save duration to avoid impacting main workload.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        initial_interval: float = 1.0,
        min_interval: float = 0.5,
        max_interval: float = 10.0,
    ):
        self.session_manager = session_manager
        self._interval = initial_interval
        self._min_interval = min_interval
        self._max_interval = max_interval

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._logger = get_logger('libercode.autosaver')

        self._adjust_threshold = 0.8
        self._consecutive_adjustments = 0

    async def _save_loop(self) -> None:
        """Background save loop."""
        while self._running:
            try:
                self.session_manager.save_async()
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Error in save loop: {e}")

            new_interval = self.session_manager.get_interval()
            if new_interval != self._interval:
                self._interval = new_interval
                self._logger.debug(f"AutoSaver interval adjusted to {self._interval:.1f}s")

    def start(self) -> None:
        """Start the auto-saver."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._save_loop())
        self._logger.info(f"AutoSaver started with interval {self._interval}s")

    async def stop(self) -> None:
        """Stop the auto-saver and wait for final save."""
        if not self._running:
            return

        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        self.session_manager.save()
        self._logger.info("AutoSaver stopped, final save completed")

    def adjust_interval(self, measured_duration: float) -> None:
        """Manually adjust interval based on observed save duration."""
        if measured_duration > self._interval * self._adjust_threshold:
            self._consecutive_adjustments += 1
            if self._consecutive_adjustments >= 3:
                new_interval = min(self._interval * 1.5, self._max_interval)
                if new_interval > self._interval:
                    self._interval = new_interval
                    self._logger.warning(
                        f"Save duration {measured_duration:.2f}s exceeds threshold, "
                        f"increasing interval to {self._interval:.1f}s"
                    )
                self._consecutive_adjustments = 0
        else:
            self._consecutive_adjustments = 0
            if self._interval > self._min_interval:
                new_interval = max(self._interval * 0.9, self._min_interval)
                if new_interval < self._interval:
                    self._interval = new_interval
                    self._logger.debug(f"Decreasing interval to {self._interval:.1f}s")


class SessionRecoveryManager:
    """
    Manages session recovery from saved sessions.
    """

    def __init__(self, session_dir: Path):
        self.session_dir = Path(session_dir) / ".libercode" / "sessions"

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all available sessions."""
        if not self.session_dir.exists():
            return []

        sessions = []
        for session_path in self.session_dir.iterdir():
            if not session_path.is_dir():
                continue

            meta_path = session_path / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    sessions.append(meta)
                except Exception:
                    sessions.append({
                        "session_name": session_path.name,
                        "error": "Failed to read meta.json"
                    })
            else:
                sessions.append({
                    "session_name": session_path.name,
                    "created_at": "unknown",
                    "updated_at": "unknown",
                })

        sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return sessions

    def load_session(self, session_name: str) -> Optional[Dict[str, Any]]:
        """Load a session and return its contents."""
        session_path = self.session_dir / session_name
        if not session_path.exists():
            return None

        session_data = {}

        meta_path = session_path / "meta.json"
        if meta_path.exists():
            session_data["meta"] = json.loads(meta_path.read_text())

        lead_path = session_path / "lead.json"
        if lead_path.exists():
            session_data["lead"] = json.loads(lead_path.read_text())

        team_config_path = session_path / "team_config.json"
        if team_config_path.exists():
            session_data["team_config"] = json.loads(team_config_path.read_text())

        tasks_path = session_path / "tasks"
        if tasks_path.exists():
            session_data["tasks"] = {}
            for task_file in tasks_path.glob("task_*.json"):
                try:
                    task_id = task_file.stem.replace("task_", "")
                    session_data["tasks"][task_id] = json.loads(task_file.read_text())
                except Exception:
                    pass

        teammates_path = session_path / "teammates"
        if teammates_path.exists():
            session_data["teammates"] = {}
            for teammate_file in teammates_path.glob("*_messages.json"):
                name = teammate_file.stem.replace("_messages", "")
                session_data["teammates"][name] = json.loads(teammate_file.read_text())

            session_data["teammate_inboxes"] = {}
            for inbox_file in teammates_path.glob("*_inbox.jsonl"):
                name = inbox_file.stem.replace("_inbox", "")
                session_data["teammate_inboxes"][name] = inbox_file.read_text()

        return session_data

    def delete_session(self, session_name: str) -> bool:
        """Delete a session."""
        session_path = self.session_dir / session_name
        if not session_path.exists():
            return False

        shutil.rmtree(session_path)
        return True

    def restore_session(
        self,
        session_name: str,
        lead,
        teammate_manager,
        task_manager,
        message_bus,
    ) -> Dict[str, Any]:
        """Restore a session into the given live components.

        Returns a summary dict with counts of restored items.
        """
        import time as _time
        from libercode.utils.logging import get_logger as _get_logger
        _logger = _get_logger('libercode.session.restore')

        session_path = self.session_dir / session_name
        if not session_path.exists():
            return {"error": f"Session '{session_name}' not found"}

        summary = {"session_name": session_name, "restored": {}}

        # Step 1: Shutdown all current teammates
        if teammate_manager:
            shutdown_results = teammate_manager.shutdown_all()
            _logger.info(f"Shutdown results: {shutdown_results}")
            summary["shutdown_results"] = shutdown_results

        # Step 2: Restore tasks
        tasks_src = session_path / "tasks"
        if task_manager and tasks_src.exists():
            task_count = task_manager.restore_from_dir(tasks_src)
            summary["restored"]["tasks"] = task_count
            _logger.info(f"Restored {task_count} tasks")

        # Step 3: Restore inbox files (lead + teammates)
        if message_bus:
            inbox_dst = message_bus.inbox_dir

            lead_inbox_src = session_path / "lead_inbox.jsonl"
            if lead_inbox_src.exists():
                shutil.copy2(lead_inbox_src, inbox_dst / "lead.jsonl")
                summary["restored"]["lead_inbox"] = True
                _logger.info("Restored lead inbox")

            teammates_path = session_path / "teammates"
            if teammates_path.exists():
                for inbox_file in teammates_path.glob("*_inbox.jsonl"):
                    name = inbox_file.stem.replace("_inbox", "")
                    shutil.copy2(inbox_file, inbox_dst / f"{name}.jsonl")
                    _logger.info(f"Restored inbox for {name}")
                summary["restored"]["teammate_inboxes"] = True

        # Step 4: Restore team config
        team_config_src = session_path / "team_config.json"
        if teammate_manager and team_config_src.exists():
            config_path = getattr(teammate_manager, 'config_path', None)
            if config_path:
                shutil.copy2(team_config_src, config_path)
            teammate_manager.reload_config()
            summary["restored"]["team_config"] = True
            _logger.info("Restored team config")

        # Step 5: Restore lead messages
        lead_src = session_path / "lead.json"
        if lead and lead_src.exists():
            try:
                lead_data = json.loads(lead_src.read_text())
                lead.messages = lead_data.get("messages", [])
                lead._agents_md_injected = True
                input_count = sum(
                    1 for m in lead.messages
                    if isinstance(m, dict) and m.get("role") == "user"
                )
                lead._input_counter = input_count
                summary["restored"]["lead_messages"] = len(lead.messages)
                _logger.info(f"Restored {len(lead.messages)} lead messages")
            except Exception as e:
                _logger.error(f"Failed to restore lead messages: {e}")
                summary["restored"]["lead_messages"] = f"error: {e}"

        # Step 6: Spawn teammates with restored history
        if teammate_manager:
            teammates_path = session_path / "teammates"
            spawned = []
            if teammate_manager._team_config.get("members"):
                for member in teammate_manager._team_config["members"]:
                    name = member["name"]
                    role = member["role"]
                    status = member.get("status", "working")

                    if status == "shutdown":
                        _logger.info(f"Skipping shutdown teammate '{name}'")
                        continue

                    restored_messages = []
                    if teammates_path.exists():
                        msg_file = teammates_path / f"{name}_messages.json"
                        if msg_file.exists():
                            try:
                                msg_data = json.loads(msg_file.read_text())
                                restored_messages = msg_data.get("messages", [])
                            except Exception as e:
                                _logger.warning(f"Failed to load messages for {name}: {e}")

                    result = teammate_manager.spawn_with_history(name, role, restored_messages)
                    spawned.append({"name": name, "role": role, "messages": len(restored_messages)})
                    _logger.info(f"Spawned teammate '{name}' with {len(restored_messages)} messages")

                    _time.sleep(0.5)

            summary["restored"]["teammates"] = spawned

        return summary

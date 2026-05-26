"""
Teammate manager for LiberCode.

Manages teammate lifecycle and configuration.
"""
import json
import threading
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from anthropic import Anthropic

from libercode.config import Config
from libercode.messaging.bus import MessageBus
from libercode.taskboard.manager import TaskManager
from libercode.core.teammate import TeammateAgent


@dataclass
class TeammateManager:
    """
    Manages teammate lifecycle and configuration.

    Attributes:
        config: Config instance
        message_bus: MessageBus instance
        task_manager: TaskManager instance
        client: Anthropic client
        team_dir: Directory for team configuration
    """

    config: Config
    message_bus: MessageBus
    task_manager: TaskManager
    client: Anthropic
    team_dir: Path
    threads: Dict[str, threading.Thread] = field(default_factory=dict)
    _teammates: Dict[str, TeammateAgent] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        """Initialize team directory and load config"""
        self.team_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.team_dir / "config.json"
        self._team_config = self._load_config()

    def _load_config(self) -> Dict:
        """Load team configuration"""
        if self.config_path.exists():
            return json.loads(self.config_path.read_text())
        return {"team_name": "default", "members": []}

    def _save_config(self) -> None:
        """Save team configuration"""
        self.config_path.write_text(json.dumps(self._team_config, indent=2, ensure_ascii=False))

    def spawn(self, name: str, role: str, initial_message: dict) -> str:
        """
        Spawn a new teammate.

        Args:
            name: Teammate name
            role: Teammate role
            initial_message: Initial message for LLM

        Returns:
            Status message
        """
        # Check if teammate already exists
        member = self._find_member(name)

        if member and member.get("status") not in ("idle", "shutdown"):
            return f"Error: '{name}' is currently {member['status']}"

        # Try to create tmux pane (optional)
        pty_file = None
        if self._is_tmux_available():
            try:
                pty_path = self._create_tmux_pane_for_teammate(name)
                pty_file = open(pty_path, 'w', buffering=1)
                if self.config.debug:
                    pty_file.write(f"Teammate {name} pane initialized.\n")
                    pty_file.flush()
            except Exception as e:
                # Tmux failed, use shared output
                pass

        # Create or update member record
        if member:
            member["status"] = "working"
        else:
            member = {
                "name": name,
                "role": role,
                "status": "working",
            }
            self._team_config["members"].append(member)

        self._save_config()

        # Create teammate agent
        teammate = TeammateAgent(
            name=name,
            role=role,
            client=self.client,
            config=self.config,
            message_bus=self.message_bus,
            task_manager=self.task_manager,
            pty_file=pty_file,
        )

        # Store teammate instance for later access
        self._teammates[name] = teammate

        # Start thread
        team_name = self._team_config.get("team_name", "default")
        thread = threading.Thread(
            target=teammate.run,
            args=(initial_message, team_name),
            daemon=True,
        )
        self.threads[name] = thread
        thread.start()

        return f"Spawned '{name}' (role: {role})" + (f" in pane" if pty_file else "")

    def spawn_with_history(self, name: str, role: str, restored_messages: list) -> str:
        """Spawn a teammate with pre-restored message history (for session recovery).

        Args:
            name: Teammate name
            role: Teammate role
            restored_messages: Full message history to restore

        Returns:
            Status message
        """
        member = self._find_member(name)

        if member and member.get("status") not in ("idle", "shutdown"):
            return f"Error: '{name}' is currently {member['status']}"

        pty_file = None
        if self._is_tmux_available():
            try:
                pty_path = self._create_tmux_pane_for_teammate(name)
                pty_file = open(pty_path, 'w', buffering=1)
                if self.config.debug:
                    pty_file.write(f"Teammate {name} pane initialized (recovered).\n")
                    pty_file.flush()
            except Exception:
                pass

        if member:
            member["status"] = "working"
        else:
            member = {
                "name": name,
                "role": role,
                "status": "working",
            }
            self._team_config["members"].append(member)

        self._save_config()

        teammate = TeammateAgent(
            name=name,
            role=role,
            client=self.client,
            config=self.config,
            message_bus=self.message_bus,
            task_manager=self.task_manager,
            pty_file=pty_file,
        )

        self._teammates[name] = teammate

        team_name = self._team_config.get("team_name", "default")
        thread = threading.Thread(
            target=teammate.run_with_history,
            args=(restored_messages, team_name),
            daemon=True,
        )
        self.threads[name] = thread
        thread.start()

        return f"Spawned '{name}' (role: {role}) [recovered]" + (f" in pane" if pty_file else "")

    def shutdown_all(self, timeout: float = 5.0) -> List[str]:
        """Shutdown all active teammates and wait for their threads.

        Args:
            timeout: Max seconds to wait per thread

        Returns:
            List of shutdown status messages
        """
        results = []

        for name, teammate in list(self._teammates.items()):
            teammate._should_shutdown = True
            self._set_status(name, "shutdown")

        for name, thread in list(self.threads.items()):
            thread.join(timeout=timeout)
            if thread.is_alive():
                results.append(f"'{name}' did not shut down within {timeout}s")
            else:
                results.append(f"'{name}' shut down")

        self._teammates.clear()
        self.threads.clear()

        return results

    def reload_config(self) -> None:
        """Reload team configuration from disk."""
        self._team_config = self._load_config()

    def _is_tmux_available(self) -> bool:
        """Check if tmux is available"""
        from libercode.ui import is_tmux_available
        return is_tmux_available()

    def _create_tmux_pane_for_teammate(self, name: str) -> str:
        """
        Create tmux pane for teammate using balanced splitting.

        Args:
            name: Teammate name (used for pane title)

        Returns:
            PTY device path

        Raises:
            TmuxError: If pane creation fails
        """
        from libercode.ui import create_balanced_pane

        # Ensure border status is enabled
        from libercode.ui import ensure_border_status
        ensure_border_status()

        # Create balanced pane with teammate name as title
        return create_balanced_pane(title_prefix=name, keep_focus=True)

    def _find_member(self, name: str) -> Optional[Dict]:
        """Find member by name"""
        for m in self._team_config.get("members", []):
            if m["name"] == name:
                return m
        return None

    def _set_status(self, name: str, status: str) -> None:
        """Update teammate status"""
        member = self._find_member(name)
        if member:
            member["status"] = status
            self._save_config()

    def list_all(self) -> str:
        """
        List all teammates with status.

        Returns:
            Human-readable teammate list
        """
        if not self._team_config.get("members"):
            return "No teammates."

        lines = [f"Team: {self._team_config.get('team_name', 'default')}"]
        for m in self._team_config["members"]:
            lines.append(f"  {m['name']} ({m['role']}): {m['status']}")

        return "\n".join(lines)

    def member_names(self) -> List[str]:
        """
        Get list of teammate names.

        Returns:
            List of names
        """
        return [m["name"] for m in self._team_config.get("members", [])]

    def get_teammate(self, name: str) -> Optional[TeammateAgent]:
        """
        Get teammate instance by name.

        Args:
            name: Teammate name

        Returns:
            TeammateAgent instance or None if not found
        """
        return self._teammates.get(name)

    def drop(self, name: str) -> str:
        """
        Drop (remove) a teammate and cleanup related data.

        Args:
            name: Teammate name

        Returns:
            Status message
        """
        member = self._find_member(name)
        if not member:
            return f"Error: '{name}' not found"

        if name in self._teammates:
            del self._teammates[name]

        if name in self.threads:
            del self.threads[name]

        self._team_config["members"] = [
            m for m in self._team_config.get("members", []) if m["name"] != name
        ]
        self._save_config()

        return f"Dropped teammate '{name}'"

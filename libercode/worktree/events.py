"""
Event bus for worktree lifecycle events.

Provides append-only event logging for observability.
"""

import json
import time
from pathlib import Path
from typing import Dict, Optional


class EventBus:
    """
    Append-only event log for worktree lifecycle tracking.

    Events are stored in JSONL format for easy streaming and analysis.
    """

    def __init__(self, event_log_path: Path):
        """
        Initialize event bus.

        Args:
            event_log_path: Path to JSONL event log file
        """
        self.path = event_log_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("")

    def emit(
        self,
        event: str,
        task: Optional[Dict] = None,
        worktree: Optional[Dict] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Emit a lifecycle event.

        Args:
            event: Event name (e.g., "worktree.create.before")
            task: Optional task context
            worktree: Optional worktree context
            error: Optional error message
        """
        payload = {
            "event": event,
            "ts": time.time(),
            "task": task or {},
            "worktree": worktree or {},
        }
        if error:
            payload["error"] = error

        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def list_recent(self, limit: int = 20) -> str:
        """
        List recent events.

        Args:
            limit: Maximum number of events to return

        Returns:
            JSON string of recent events
        """
        limit = max(1, min(int(limit or 20), 200))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        recent = lines[-limit:]

        items = []
        for line in recent:
            try:
                items.append(json.loads(line))
            except Exception:
                items.append({"event": "parse_error", "raw": line})

        return json.dumps(items, indent=2, ensure_ascii=False)

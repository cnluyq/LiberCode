"""
Status pane for LiberCode.

Displays execution process information in a dedicated tmux pane,
including task board and LLM context/token usage.
"""

import json
import threading
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from libercode.taskboard.models import Task, TaskStatus
from libercode.utils.token_tracker import TokenTracker
from libercode.ui.tmux import create_balanced_pane, get_pane_by_tty, ensure_border_status


CONTEXT_WINDOW_SIZE = 1_000_000

def _load_lead_system_prompt(workdir: Path) -> str:
    path = Path(__file__).parent.parent / "prompts" / "lead_system.txt"
    return path.read_text(encoding="utf-8").format(workdir=workdir)

def _load_teammate_system_prompt(name: str, role: str, workdir: Path) -> str:
    path = Path(__file__).parent.parent / "prompts" / "teammate_system.txt"
    return path.read_text(encoding="utf-8").format(
        name=name, role=role, team_name="default", workdir=workdir
    )

def _text_tokens(text: str) -> int:
    return len(text.encode()) // 2


def _count_agent_tokens(messages: List[Dict], system: str) -> int:
    if not messages:
        return 0
    total = _text_tokens(system)
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if hasattr(block, "text"):
                    total += _text_tokens(block.text)
                elif isinstance(block, dict):
                    total += _text_tokens(str(block.get("text", "")))
        elif isinstance(content, str):
            total += _text_tokens(content)
        total += _text_tokens(msg.get("role", "user"))
    return total


class StatusPane:
    """
    Displays execution process info in a dedicated tmux pane.

    Refreshes periodically with:
    - Todo area: task board with Kanban-style columns
    - Context area: per-agent context usage and total token stats
    """

    def __init__(
        self,
        task_manager,
        teammate_manager,
        lead,
        refresh_interval: float = 1.0,
        pane_title: str = "Status",
    ):
        self.task_manager = task_manager
        self.teammate_manager = teammate_manager
        self.lead = lead
        self.refresh_interval = refresh_interval
        self.pane_title = pane_title
        self._file: Optional[Any] = None
        self._pane_id: Optional[str] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        ensure_border_status()
        tty_path = create_balanced_pane(self.pane_title)
        self._pane_id = get_pane_by_tty(tty_path)
        self._file = open(tty_path, "w", buffering=1)
        self._thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._file:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None

    def _write(self, content: str) -> None:
        if self._file:
            try:
                self._file.write(content + "\n")
                self._file.flush()
            except OSError:
                pass

    def _clear(self) -> None:
        self._write("\033[2J\033[3J\033[H")

    def _load_tasks(self) -> List[Task]:
        tasks = []
        for f in sorted(self.task_manager.tasks_dir.glob("task_*.json")):
            tasks.append(Task.from_dict(json.loads(f.read_text())))
        return tasks

    def _format_tasks(self) -> str:
        tasks = self._load_tasks()
        status_symbol = {
            TaskStatus.IN_PROGRESS: ("\033[1;33m[>]\033[0m", "in_progress"),
            TaskStatus.PENDING: ("\033[90m[ ]\033[0m", "pending"),
            TaskStatus.COMPLETED: ("\033[32m[✓]\033[0m", "completed"),
        }

        lines = ["\033[1;36m=== Todo Board ===\033[0m"]
        if not tasks:
            lines.append("  (no tasks)")
            return "\n".join(lines)

        for t in tasks:
            sym, _ = status_symbol.get(t.status, ("[?]", "unknown"))
            extra_parts = []
            if t.assigned_to:
                extra_parts.append(f"\033[90m→ {t.assigned_to}\033[0m")
            if t.blocked_by:
                extra_parts.append(f"\033[31m⚠ blocked by {t.blocked_by}\033[0m")
            extra = "  " + "  ".join(extra_parts) if extra_parts else ""
            lines.append(f"  {sym} \033[37m#{t.id}\033[0m  {t.subject[:50]}{extra}")

        return "\n".join(lines)

    def _format_context(self) -> str:
        tracker = TokenTracker.get_tracker()
        total_summary = tracker.get_total_summary()
        caller_summary = tracker.get_caller_summary()
        lead_system = _load_lead_system_prompt(self.lead.config.workdir)

        lines = ["\033[1;36m=== Context & Tokens ===\033[0m", ""]

        lines.append("\033[1;33m── Lead ──\033[0m")
        lead_tokens = _count_agent_tokens(self.lead.messages, lead_system)
        lead_ratio = min(lead_tokens / CONTEXT_WINDOW_SIZE, 1.0)
        lead_record = caller_summary.get("lead", {})
        lines.append(f"  Context:\033[37m{lead_ratio*100:.0f}%\033[0m used(\033[37m{lead_tokens:,}\033[0m/\033[37m{CONTEXT_WINDOW_SIZE:,}\033[0m)  Message: \033[37m{len(self.lead.messages)}\033[0m")
        lines.append(f"  Consumed Tokens: \033[37m{lead_record.get('input_tokens', 0):,}\033[0min / \033[37m{lead_record.get('output_tokens', 0):,}\033[0mout")

        lines.append("")
        lines.append("\033[1;33m── Teammates ──\033[0m")
        teammate_names = self.teammate_manager.member_names()
        if not teammate_names:
            lines.append("  (none)")
        else:
            for name in teammate_names:
                member = self.teammate_manager._find_member(name)
                status = member.get("status", "unknown") if member else "unknown"
                teammate = self.teammate_manager.get_teammate(name)
                if teammate:
                    tm_system = _load_teammate_system_prompt(name, member.get("role", ""), teammate.config.workdir)
                    tokens = _count_agent_tokens(teammate.messages, tm_system)
                else:
                    tokens = 0
                ratio = min(tokens / CONTEXT_WINDOW_SIZE, 1.0)
                tm_record = caller_summary.get(name, {})
                status_color = {"working": "32", "idle": "33", "shutdown": "31"}.get(status, "37")
                lines.append(f"  \033[1;{status_color}m{name}\033[0m(\033[90m{status}\033[0m)")
                lines.append(f"    Context:\033[37m{ratio*100:.0f}%\033[0m used(\033[37m{tokens:,}\033[0m/\033[37m{CONTEXT_WINDOW_SIZE:,}\033[0m)  Message: \033[37m{len(teammate.messages) if teammate else 0}\033[0m")
                lines.append(f"    Consumed Tokens: \033[37m{tm_record.get('input_tokens', 0):,}\033[0min / \033[37m{tm_record.get('output_tokens', 0):,}\033[0mout")

        lines.append("")
        lines.append("\033[1;33m── Token Summary ──\033[0m")
        lines.append(f"  Total calls:    \033[37m{total_summary.get('call_count', 0)}\033[0m")
        lines.append(f"  Input tokens:   \033[37m{total_summary.get('input_tokens', 0):,}\033[0m")
        lines.append(f"  Output tokens:  \033[37m{total_summary.get('output_tokens', 0):,}\033[0m")
        lines.append(f"  Cache read:     \033[37m{total_summary.get('cache_read_input_tokens', 0):,}\033[0m")
        lines.append(f"  Cache create:   \033[37m{total_summary.get('cache_creation_input_tokens', 0):,}\033[0m")
        lines.append(f"  \033[1;37mTotal tokens:   {total_summary.get('total_tokens', 0):,}\033[0m")

        return "\n".join(lines)

    def _render(self) -> None:
        self._clear()
        self._write(self._format_tasks())
        self._write("")
        self._write(self._format_context())
        self._write("")
        self._write(f"\033[90mRefresh: {self.refresh_interval}s | Updated: {time.strftime('%H:%M:%S')}\033[0m")

    def _refresh_loop(self) -> None:
        while self._running:
            try:
                self._render()
            except Exception:
                pass
            time.sleep(self.refresh_interval)

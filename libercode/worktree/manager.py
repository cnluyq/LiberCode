"""
Worktree manager for LiberCode.

Manages git worktrees for directory-level task isolation.
"""

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional, List

from libercode.worktree.events import EventBus
from libercode.taskboard.manager import TaskManager
from libercode.exceptions import LiberCodeError


class WorktreeError(LiberCodeError):
    """Raised when worktree operations fail."""
    pass


class WorktreeManager:
    """
    Manages git worktrees for parallel task execution.

    Worktrees provide directory-level isolation - each worktree is an
    independent checkout of the repository on its own branch.

    Key insight: "Isolate by directory, coordinate by task ID."
    """

    def __init__(
        self,
        repo_root: Path,
        tasks: TaskManager,
        events: EventBus,
    ):
        """
        Initialize worktree manager.

        Args:
            repo_root: Git repository root
            tasks: Task manager instance
            events: Event bus instance
        """
        self.repo_root = repo_root
        self.tasks = tasks
        self.events = events
        self.dir = repo_root / ".worktrees"
        self.dir.mkdir(parents=True, exist_ok=True)

        self.index_path = self.dir / "index.json"
        if not self.index_path.exists():
            self.index_path.write_text(json.dumps({"worktrees": []}, indent=2, ensure_ascii=False))

        self.git_available = self._is_git_repo()

    def _is_git_repo(self) -> bool:
        """Check if current directory is inside a git repository."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _run_git(self, args: List[str]) -> str:
        """
        Run git command in repository root.

        Args:
            args: Git command arguments

        Returns:
            Command output

        Raises:
            WorktreeError: If git command fails
        """
        if not self.git_available:
            raise WorktreeError("Not in a git repository. Worktree tools require git.")

        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                msg = (result.stdout + result.stderr).strip()
                raise WorktreeError(msg or f"git {' '.join(args)} failed")
            return (result.stdout + result.stderr).strip() or "(no output)"
        except subprocess.TimeoutExpired:
            raise WorktreeError(f"Git command timeout: git {' '.join(args)}")

    def _load_index(self) -> Dict:
        """Load worktree index."""
        return json.loads(self.index_path.read_text())

    def _save_index(self, data: Dict) -> None:
        """Save worktree index."""
        self.index_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def _find(self, name: str) -> Optional[Dict]:
        """
        Find worktree entry by name.

        Args:
            name: Worktree name

        Returns:
            Worktree entry dict or None
        """
        idx = self._load_index()
        for wt in idx.get("worktrees", []):
            if wt.get("name") == name:
                return wt
        return None

    def _validate_name(self, name: str) -> None:
        """
        Validate worktree name format.

        Args:
            name: Worktree name

        Raises:
            WorktreeError: If name is invalid
        """
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", name or ""):
            raise WorktreeError(
                "Invalid worktree name. Use 1-40 chars: letters, numbers, ., _, -"
            )

    def create(
        self,
        name: str,
        task_id: Optional[int] = None,
        base_ref: str = "HEAD",
    ) -> Dict:
        """
        Create a git worktree and optionally bind it to a task.

        Args:
            name: Unique worktree name
            task_id: Optional task ID to bind
            base_ref: Git ref to branch from (default: HEAD)

        Returns:
            Worktree entry dict

        Raises:
            WorktreeError: If creation fails
        """
        self._validate_name(name)

        if self._find(name):
            raise WorktreeError(f"Worktree '{name}' already exists in index")

        if task_id is not None:
            try:
                self.tasks.get(task_id)
            except Exception:
                raise WorktreeError(f"Task {task_id} not found")

        path = self.dir / name
        branch = f"wt/{name}"

        self.events.emit(
            "worktree.create.before",
            task={"id": task_id} if task_id is not None else {},
            worktree={"name": name, "base_ref": base_ref},
        )

        try:
            self._run_git(["worktree", "add", "-b", branch, str(path), base_ref])

            entry = {
                "name": name,
                "path": str(path),
                "branch": branch,
                "task_id": task_id,
                "status": "active",
                "created_at": time.time(),
            }

            idx = self._load_index()
            idx["worktrees"].append(entry)
            self._save_index(idx)

            # Bind to task if provided
            if task_id is not None:
                # Update task with worktree binding
                task = self.tasks.get(task_id)
                if hasattr(task, 'worktree'):
                    # Task model has worktree field
                    from libercode.taskboard.models import TaskStatus
                    self.tasks.update(
                        task_id,
                        status=TaskStatus.IN_PROGRESS if task.status == TaskStatus.PENDING else None,
                    )

            self.events.emit(
                "worktree.create.after",
                task={"id": task_id} if task_id is not None else {},
                worktree={
                    "name": name,
                    "path": str(path),
                    "branch": branch,
                    "status": "active",
                },
            )

            return entry
        except Exception as e:
            self.events.emit(
                "worktree.create.failed",
                task={"id": task_id} if task_id is not None else {},
                worktree={"name": name, "base_ref": base_ref},
                error=str(e),
            )
            raise

    def list_all(self) -> str:
        """
        List all worktrees in index.

        Returns:
            Human-readable worktree list
        """
        idx = self._load_index()
        wts = idx.get("worktrees", [])

        if not wts:
            return "No worktrees in index."

        lines = []
        for wt in wts:
            suffix = f" task={wt['task_id']}" if wt.get("task_id") else ""
            lines.append(
                f"[{wt.get('status', 'unknown')}] {wt['name']} -> "
                f"{wt['path']} ({wt.get('branch', '-')}){suffix}"
            )

        return "\n".join(lines)

    def status(self, name: str) -> str:
        """
        Show git status for a worktree.

        Args:
            name: Worktree name

        Returns:
            Git status output
        """
        wt = self._find(name)
        if not wt:
            return f"Error: Unknown worktree '{name}'"

        path = Path(wt["path"])
        if not path.exists():
            return f"Error: Worktree path missing: {path}"

        try:
            result = subprocess.run(
                ["git", "status", "--short", "--branch"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=60,
            )
            text = (result.stdout + result.stderr).strip()
            return text or "Clean worktree"
        except subprocess.TimeoutExpired:
            return "Error: Git status timeout"

    def run(self, name: str, command: str) -> str:
        """
        Run shell command in a worktree directory.

        Args:
            name: Worktree name
            command: Shell command to execute

        Returns:
            Command output (truncated to 50,000 chars)
        """
        # Block dangerous commands
        dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
        if any(d in command for d in dangerous):
            return "Error: Dangerous command blocked"

        wt = self._find(name)
        if not wt:
            return f"Error: Unknown worktree '{name}'"

        path = Path(wt["path"])
        if not path.exists():
            return f"Error: Worktree path missing: {path}"

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=path,
                capture_output=True,
                text=True,
                timeout=300,
            )
            out = (result.stdout + result.stderr).strip()
            return out[:50000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (300s)"

    def remove(
        self,
        name: str,
        force: bool = False,
        complete_task: bool = False,
    ) -> str:
        """
        Remove a worktree and optionally complete its bound task.

        Args:
            name: Worktree name
            force: Force removal even with uncommitted changes
            complete_task: Mark bound task as completed

        Returns:
            Status message

        Raises:
            WorktreeError: If removal fails
        """
        wt = self._find(name)
        if not wt:
            return f"Error: Unknown worktree '{name}'"

        self.events.emit(
            "worktree.remove.before",
            task={"id": wt.get("task_id")} if wt.get("task_id") is not None else {},
            worktree={"name": name, "path": wt.get("path")},
        )

        try:
            args = ["worktree", "remove"]
            if force:
                args.append("--force")
            args.append(wt["path"])
            self._run_git(args)

            # Complete task if requested
            if complete_task and wt.get("task_id") is not None:
                task_id = wt["task_id"]
                from libercode.taskboard.models import TaskStatus
                self.tasks.update(task_id, status=TaskStatus.COMPLETED)

                self.events.emit(
                    "task.completed",
                    task={
                        "id": task_id,
                        "status": "completed",
                    },
                    worktree={"name": name},
                )

            # Update index
            idx = self._load_index()
            for item in idx.get("worktrees", []):
                if item.get("name") == name:
                    item["status"] = "removed"
                    item["removed_at"] = time.time()
            self._save_index(idx)

            self.events.emit(
                "worktree.remove.after",
                task={"id": wt.get("task_id")} if wt.get("task_id") is not None else {},
                worktree={"name": name, "path": wt.get("path"), "status": "removed"},
            )

            return f"Removed worktree '{name}'"
        except Exception as e:
            self.events.emit(
                "worktree.remove.failed",
                task={"id": wt.get("task_id")} if wt.get("task_id") is not None else {},
                worktree={"name": name, "path": wt.get("path")},
                error=str(e),
            )
            raise

    def keep(self, name: str) -> Dict:
        """
        Mark a worktree as kept in lifecycle state.

        This doesn't remove the worktree, just marks it for manual review.

        Args:
            name: Worktree name

        Returns:
            Updated worktree entry
        """
        wt = self._find(name)
        if not wt:
            raise WorktreeError(f"Unknown worktree '{name}'")

        idx = self._load_index()
        kept = None
        for item in idx.get("worktrees", []):
            if item.get("name") == name:
                item["status"] = "kept"
                item["kept_at"] = time.time()
                kept = item
        self._save_index(idx)

        self.events.emit(
            "worktree.keep",
            task={"id": wt.get("task_id")} if wt.get("task_id") is not None else {},
            worktree={
                "name": name,
                "path": wt.get("path"),
                "status": "kept",
            },
        )

        return kept

"""
Task management for LiberCode.
Provides CRUD operations for tasks with dependency tracking.
"""

import json
import shutil
from pathlib import Path
from typing import List, Optional

from libercode.taskboard.models import Task, TaskStatus
from libercode.exceptions import TaskNotFoundError


class TaskManager:
    """
    Manages task persistence and lifecycle.
    Tasks are stored as JSON files in a directory.
    """

    def __init__(self, tasks_dir: Path):
        """
        Initialize task manager.

        Args:
            tasks_dir: Directory to store task JSON files
        """
        self.tasks_dir = tasks_dir
        self.tasks_dir.mkdir(exist_ok=True, parents=True)
        self._next_id = self._max_id() + 1

    def _max_id(self) -> int:
        """Get maximum task ID from existing files"""
        ids = [
            int(f.stem.split("_")[1])
            for f in self.tasks_dir.glob("task_*.json")
        ]
        return max(ids) if ids else 0

    def _load(self, task_id: int) -> Task:
        """
        Load task from file.

        Args:
            task_id: Task ID to load

        Returns:
            Task instance

        Raises:
            TaskNotFoundError: If task doesn't exist
        """
        path = self.tasks_dir / f"task_{task_id}.json"
        if not path.exists():
            raise TaskNotFoundError(f"Task {task_id} not found")
        return Task.from_dict(json.loads(path.read_text()))

    def _save(self, task: Task) -> None:
        """
        Save task to file.

        Args:
            task: Task to save
        """
        path = self.tasks_dir / f"task_{task.id}.json"
        path.write_text(json.dumps(task.to_dict(), indent=2))

    def create(
            self,
            subject: str,
            description: str = "",
            required_role: str = "",
            assigned_to: str = "",
        ) -> Task:
        """
        Create a new task.

        Args:
            subject: Task title
            description: Optional detailed description
            required_role: Required teammate role (e.g. 'frontend', 'backend')
            assigned_to: Specific teammate name to assign

        Returns:
            Created task with auto-assigned ID
        """
        task = Task(
            id=self._next_id,
            subject=subject,
            description=description,
            required_role=required_role,
            assigned_to=assigned_to,
        )
        self._save(task)
        self._next_id += 1
        return task

    def get(self, task_id: int) -> Task:
        """
        Get task by ID.

        Args:
            task_id: Task ID to retrieve

        Returns:
            Task instance

        Raises:
            TaskNotFoundError: If task doesn't exist
        """
        return self._load(task_id)

    def update(
        self,
        task_id: int,
        status: Optional[TaskStatus] = None,
        add_blocked_by: Optional[List[int]] = None,
        add_blocks: Optional[List[int]] = None,
        worktree: Optional[str] = None,
        owner: Optional[str] = None,
        required_role: Optional[str] = None,
        assigned_to: Optional[str] = None,
    ) -> Task:
        """
        Update task status, dependencies, or worktree binding.

        Args:
            task_id: Task ID to update
            status: Optional new status
            add_blocked_by: Task IDs that block this task
            add_blocks: Task IDs that this task blocks
            worktree: Optional worktree name to bind
            owner: Optional owner name
            required_role: Optional required teammate role
            assigned_to: Optional specific teammate to assign

        Returns:
            Updated task

        Raises:
            TaskNotFoundError: If task doesn't exist
            ValueError: If status is invalid
        """
        task = self._load(task_id)

        if status:
            if status not in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED):
                raise ValueError(f"Invalid status: {status}")
            task.status = status

        # When completed, remove from all other tasks' blockedBy
        if status == TaskStatus.COMPLETED:
            self._clear_dependency(task_id)

        if add_blocked_by:
            task.blocked_by = list(set(task.blocked_by + add_blocked_by))

            # Bidirectional: update blocking tasks
            for blocking_id in add_blocked_by:
                try:
                    blocking = self._load(blocking_id)
                    if task_id not in blocking.blocks:
                        blocking.blocks.append(task_id)
                        self._save(blocking)
                except TaskNotFoundError:
                    pass # Ignore if blocking task doesn't exist

        if add_blocks:
            task.blocks = list(set(task.blocks + add_blocks))

            # Bidirectional: update blocked tasks
            for blocked_id in add_blocks:
                try:
                    blocked = self._load(blocked_id)
                    if task_id not in blocked.blocked_by:
                        blocked.blocked_by.append(task_id)
                        self._save(blocked)
                except TaskNotFoundError:
                    pass # Ignore if blocked task doesn't exist

        if worktree is not None:
            task.worktree = worktree
            # Auto-progress status if binding worktree to pending task
            if worktree and task.status == TaskStatus.PENDING:
                task.status = TaskStatus.IN_PROGRESS

        if owner is not None:
            task.owner = owner

        if required_role is not None:
            task.required_role = required_role

        if assigned_to is not None:
            task.assigned_to = assigned_to

        self._save(task)
        return task

    def _clear_dependency(self, completed_id: int) -> None:
        """
        Remove completed task from all dependency lists.

        Args:
            completed_id: ID of completed task
        """
        for f in self.tasks_dir.glob("task_*.json"):
            task = Task.from_dict(json.loads(f.read_text()))
            if completed_id in task.blocked_by:
                task.blocked_by.remove(completed_id)
                self._save(task)

    def list_all(self) -> str:
        """
        List all tasks with status summary.

        Returns:
            Human-readable task list
        """
        tasks = [
            Task.from_dict(json.loads(f.read_text()))
            for f in sorted(self.tasks_dir.glob("task_*.json"))
        ]

        if not tasks:
            return "No tasks."

        lines = []
        for task in tasks:
            marker = {
                TaskStatus.PENDING: "[ ]",
                TaskStatus.IN_PROGRESS: "[>]",
                TaskStatus.COMPLETED: "[x]",
            }.get(task.status, "[?]")

            blocked = (
                f" (blocked by: {task.blocked_by})"
                if task.blocked_by
                else ""
            )
            lines.append(f"{marker} #{task.id}: {task.subject}{blocked}")

        return "\n".join(lines)

    def restore_from_dir(self, source_dir: Path) -> int:
        """Restore tasks from a session backup directory.

        Clears existing tasks and copies from source. Returns count of restored tasks.
        """
        if not source_dir.exists():
            return 0

        for existing in self.tasks_dir.glob("task_*.json"):
            existing.unlink()

        count = 0
        for src_file in sorted(source_dir.glob("task_*.json")):
            shutil.copy2(src_file, self.tasks_dir / src_file.name)
            count += 1

        self._next_id = self._max_id() + 1
        return count

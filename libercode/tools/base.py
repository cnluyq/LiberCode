"""
Base tools for LiberCode.

Provides foundational tools for bash commands and file operations.
"""
import subprocess
from pathlib import Path
from typing import Optional
from libercode.exceptions import PathEscapeError, DangerousCommandError


def safe_path(path: str, workdir: Path) -> Path:
    """
    Validate path doesn't escape workspace.

    Args:
        path: Relative or absolute path to validate
        workdir: Workspace root directory

    Returns:
        Absolute path within workdir

    Raises:
        PathEscapeError: If path escapes workspace
    """
    resolved = (workdir / path).resolve()
    if not resolved.is_relative_to(workdir):
        raise PathEscapeError(f"Path escapes workspace: {path}")
    return resolved


def run_bash(command: str, timeout: int = 120) -> str:
    """
    Execute bash command with safety checks.

    Args:
        command: Shell command to execute
        timeout: Timeout in seconds (default: 120)

    Returns:
        Command output (truncated to 50,000 chars)

    Raises:
        DangerousCommandError: If command is blocked
    """
    # Block dangerous commands
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
    if any(d in command for d in dangerous):
        raise DangerousCommandError(f"Blocked dangerous command: {command}")

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        # Truncate output
        return output[:50000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Timeout ({timeout}s)"


def read_file(path: str, limit: Optional[int] = None) -> str:
    """
    Read file contents with optional line limit.

    Args:
        path: File path
        limit: Maximum number of lines (optional)

    Returns:
        File contents
    """
    try:
        lines = Path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def write_file(path: str, content: str) -> str:
    """
    Write content to file.

    Args:
        path: File path
        content: Content to write

    Returns:
        Status message
    """
    try:
        fp = Path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"


def edit_file(path: str, old_text: str, new_text: str) -> str:
    """
    Replace exact text in file.

    Args:
        path: File path
        old_text: Text to replace
        new_text: Replacement text

    Returns:
        Status message
    """
    try:
        fp = Path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

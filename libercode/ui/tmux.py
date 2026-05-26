"""
Tmux integration for LiberCode.

Provides tmux pane management for agent output isolation.
Each agent can run in its own tmux pane with visual separation.
"""

import subprocess
import time
import threading
from pathlib import Path
from typing import List, Tuple, Optional


class TmuxError(Exception):
    """Raised when tmux operations fail."""
    pass


# Global pane counter for unique pane numbering
_pane_counter = 0
_pane_lock = threading.Lock()

# Global direction tracking for balanced splitting
_last_direction = None
_direction_lock = threading.Lock()


def ensure_border_status() -> None:
    """
    Ensure current tmux window has pane border status enabled.

    Sets pane-border-status to 'bottom' and pane-border-format to show pane title.
    Safe to call multiple times.
    """
    subprocess.run(
        ["tmux", "set", "-g", "pane-border-status", "bottom"],
        capture_output=True,
    )
    subprocess.run(
        ["tmux", "set", "-g", "pane-border-format", "#{pane_title}"],
        capture_output=True,
    )


def is_tmux_available() -> bool:
    """
    Check if running inside a tmux session.

    Returns:
        True if TMUX environment variable is set
    """
    import os
    return os.environ.get("TMUX") is not None


def get_current_panes() -> List[Tuple[str, int, int]]:
    """
    Get information about all panes in current tmux window.

    Returns:
        List of tuples (pane_id, width, height)

    Raises:
        TmuxError: If tmux command fails
    """
    cmd = ["tmux", "list-panes", "-F", "#{pane_id} #{pane_width} #{pane_height}"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    if result.returncode != 0:
        raise TmuxError(f"Failed to list panes: {result.stderr}")

    panes = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split()
        if len(parts) == 3:
            pane_id, width, height = parts[0], int(parts[1]), int(parts[2])
            panes.append((pane_id, width, height))

    return panes


def set_pane_title(pane_target: str, title: str) -> None:
    """
    Set title for a tmux pane.

    Args:
        pane_target: Pane ID or target specifier
        title: Title to display in pane border

    Raises:
        TmuxError: If tmux command fails
    """
    result = subprocess.run(
        ["tmux", "select-pane", "-T", title, "-t", pane_target],
        capture_output=True,
        timeout=5,
    )
    # Don't raise on error - title setting is optional


def create_tmux_pane(
    target_pane: Optional[str] = None,
    direction: str = "h",
    title: Optional[str] = None,
    keep_focus: bool = False,
) -> Tuple[str, str]:
    """
    Create a new tmux pane by splitting an existing one.

    Args:
        target_pane: Pane to split (None for current pane)
        direction: Split direction - 'h' for horizontal (left/right),
                   'v' for vertical (top/bottom)
        title: Optional title for the new pane
        keep_focus: If True, keep focus on the original pane instead of new one

    Returns:
        Tuple of (pane_id, tty_path)

    Raises:
        TmuxError: If pane creation fails
        ValueError: If direction is invalid
    """
    if direction not in ("h", "v"):
        raise ValueError("direction must be 'h' or 'v'")

    # Capture current pane before splitting
    current_pane = None
    if keep_focus:
        try:
            result = subprocess.run(
                ["tmux", "display-message", "-p", "#{pane_id}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                current_pane = result.stdout.strip()
        except Exception:
            pass

    # Build split-window command
    split_flag = "-h" if direction == "h" else "-v"
    cmd = [
        "tmux", "split-window",
        split_flag,
        "-P",  # Print info about new pane
        "-F", "#{pane_id} #{pane_tty}",  # Format: pane_id and tty
        "sleep", "infinity",  # Keep pane alive
    ]

    # Add target if specified
    if target_pane is not None:
        cmd.insert(2, "-t")
        cmd.insert(3, target_pane)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    if result.returncode != 0:
        raise TmuxError(f"tmux split-window failed: {result.stderr}")

    output = result.stdout.strip()
    parts = output.split()

    if len(parts) != 2:
        raise TmuxError(f"Unexpected split-window output: {output}")

    new_pane_id, tty_path = parts[0], parts[1]

    # Wait for pane to initialize
    time.sleep(0.1)

    # Set title if provided
    if title is not None:
        set_pane_title(new_pane_id, title)

    # Restore focus to original pane if requested
    if keep_focus and current_pane:
        subprocess.run(
            ["tmux", "select-pane", "-t", current_pane],
            capture_output=True,
            timeout=5,
        )

    return new_pane_id, tty_path


def create_balanced_pane(title_prefix: str = "Pane", keep_focus: bool = False) -> str:
    """
    Create a new tmux pane using balanced splitting strategy.

    This function:
    1. Finds the largest pane in current window
    2. Alternates split direction (horizontal/vertical) for better layout
    3. Sets unique pane title with counter

    Args:
        title_prefix: Prefix for pane title (default: "Pane")
        keep_focus: If True, keep focus on the original pane instead of new one

    Returns:
        PTY device path for the new pane

    Raises:
        TmuxError: If pane creation fails
    """
    global _last_direction, _pane_counter

    # Get current panes
    try:
        panes = get_current_panes()
    except TmuxError:
        panes = []

    # Get unique pane number
    with _pane_lock:
        _pane_counter += 1
        pane_num = _pane_counter

    title = f"pane {pane_num}:{title_prefix}"

    if not panes:
        # No panes found (shouldn't happen), create in current pane
        _, tty_path = create_tmux_pane(title=title, keep_focus=keep_focus)
        return tty_path

    # Find largest pane (by area)
    largest = max(panes, key=lambda p: p[1] * p[2])
    target_id, width, height = largest

    # Determine split direction (alternate from last)
    with _direction_lock:
        if _last_direction is None:
            # First split: choose based on pane aspect ratio
            direction = "h" if width >= height else "v"
        else:
            # Alternate direction
            direction = "v" if _last_direction == "h" else "h"

        _last_direction = direction

    # Create pane
    _, tty_path = create_tmux_pane(
        target_pane=target_id,
        direction=direction,
        title=title,
        keep_focus=keep_focus,
    )

    return tty_path


def close_tmux_pane(pane_id: str) -> None:
    """
    Close a tmux pane.

    Args:
        pane_id: Pane ID to close

    Raises:
        TmuxError: If pane doesn't exist or close fails
    """
    result = subprocess.run(
        ["tmux", "kill-pane", "-t", pane_id],
        capture_output=True,
        text=True,
        timeout=5,
    )

    if result.returncode != 0:
        # Pane might already be closed, don't raise
        pass


def get_pane_by_tty(tty_path: str) -> Optional[str]:
    """
    Find pane ID by PTY device path.

    Args:
        tty_path: PTY device path (e.g., /dev/ttys003)

    Returns:
        Pane ID or None if not found
    """
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-F", "#{pane_id} #{pane_tty}"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == tty_path:
                return parts[0]
    except Exception:
        pass

    return None


class TmuxPaneContext:
    """
    Context manager for temporary tmux panes.

    Automatically closes pane on exit.

    Example:
        with TmuxPaneContext("worker-1") as tty_path:
            # Use tty_path for output
            with open(tty_path, 'w') as f:
                f.write("Work in progress\\n")
    """

    def __init__(self, title: str = "Temporary"):
        """
        Initialize pane context.

        Args:
            title: Pane title
        """
        self.title = title
        self.tty_path: Optional[str] = None
        self.pane_id: Optional[str] = None

    def __enter__(self) -> str:
        """Create pane and return PTY path"""
        self.pane_id, self.tty_path = create_balanced_pane(self.title)
        return self.tty_path

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close pane"""
        if self.pane_id:
            close_tmux_pane(self.pane_id)
        return False

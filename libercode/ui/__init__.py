"""
UI components for LiberCode.

Provides output management and tmux integration.
"""

from libercode.ui.output import OutputManager, tprint
from libercode.ui.tmux import (
    TmuxError,
    TmuxPaneContext,
    ensure_border_status,
    is_tmux_available,
    get_current_panes,
    set_pane_title,
    create_tmux_pane,
    create_balanced_pane,
    close_tmux_pane,
    get_pane_by_tty,
)
from libercode.ui.status_pane import StatusPane

__all__ = [
    # Output management
    "OutputManager",
    "tprint",
    # Tmux integration
    "TmuxError",
    "TmuxPaneContext",
    "ensure_border_status",
    "is_tmux_available",
    "get_current_panes",
    "set_pane_title",
    "create_tmux_pane",
    "create_balanced_pane",
    "close_tmux_pane",
    "get_pane_by_tty",
    # Status pane
    "StatusPane",
]

"""Interrupt handling for cancellation of LLM operations."""

import threading

_cancel_event = threading.Event()


def request_cancel():
    """Request cancellation of current LLM operation."""
    _cancel_event.set()


def check_cancel() -> bool:
    """Check if cancellation was requested."""
    return _cancel_event.is_set()


def clear_cancel():
    """Clear cancellation flag before starting new operation."""
    _cancel_event.clear()
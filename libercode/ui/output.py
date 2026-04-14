"""
Thread-safe output management for LiberCode.

Provides thread-local output redirection so each agent can write to its own destination.
"""

import sys
import threading
from typing import TextIO, Optional
from contextlib import contextmanager

# Thread-local storage for output targets
_thread_output = threading.local()


class OutputManager:
    """
    Manages thread-local output redirection.

    Each thread can have its own output target (e.g., a PTY file or StringIO).
    """

    def set_target(self, target: Optional[TextIO]) -> None:
        """
        Set output target for current thread.

        Args:
            target: File-like object to write to, or None for stdout
        """
        _thread_output.target = target

    def get_target(self) -> Optional[TextIO]:
        """Get current thread's output target"""
        return getattr(_thread_output, "target", None)

    @contextmanager
    def redirect(self, target: TextIO):
        """
        Context manager for temporary output redirection.

        Args:
            target: File-like object to redirect to

        Example:
            with manager.redirect(buffer):
                tprint("Goes to buffer")
        """
        old_target = self.get_target()
        self.set_target(target)
        try:
            yield
        finally:
            self.set_target(old_target)

    @staticmethod
    def tprint(*args, **kwargs) -> None:
        """
        Thread-safe print function.

        Writes to current thread's output target (or stdout if not set).

        Args:
            *args: Values to print
            **kwargs: print() arguments (sep, end, flush)
        """
        # Get current thread's target
        out = getattr(_thread_output, "target", None)
        if out is None:
            # No target set, use builtin print
            return print(*args, **kwargs)

        # Write to custom target
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        flush = kwargs.get("flush", False)

        # Build output string
        line = sep.join(str(arg) for arg in args) + end
        out.write(line)
        if flush:
            out.flush()


# Global function for convenience
tprint = OutputManager.tprint

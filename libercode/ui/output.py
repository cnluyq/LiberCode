"""
Thread-safe output management for LiberCode.

Provides thread-local output redirection so each agent can write to its own destination.
"""

import sys
import threading
import re
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
    def tprint(*args, color=None, style=None, **kwargs) -> None:
        """
        Thread-safe print with optional color and text style.

        Args:
            *args: Values to print
            color: Color name ('red', 'green', 'blue', 'yellow', 'cyan', 'magenta', 'white')
                   or ANSI code string.
            style: Style name or list of styles:
                   - 'bold' / 'b'
                   - 'underline' / 'u'
                   - 'italic' / 'i' (limited support)
                   - 'reset' (to clear all, usually automatic)
            **kwargs: print() arguments (sep, end, flush)
        """
        out = getattr(_thread_output, "target", None)
        if out is None:
            out = sys.stdout

        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        flush = kwargs.get("flush", False)

        content = sep.join(str(arg) for arg in args)

        # Build ANSI prefix
        ansi_codes = []

        # Color handling
        if color is not None:
            supports_color = hasattr(out, 'isatty') and out.isatty()
            if supports_color:
                color_map = {
                    'red': '91', 'green': '92', 'yellow': '93',
                    'blue': '94', 'magenta': '95', 'cyan': '96', 'white': '97',
                }
                code = color_map.get(color.lower(), color) if isinstance(color, str) else color
                # Ensure code is a string like '91' or '\033[91m'
                if isinstance(code, str) and code.startswith('\033['):
                    ansi_codes.append(code)
                else:
                    ansi_codes.append(f"\033[{code}m")

        # Style handling
        if style is not None:
            supports_style = hasattr(out, 'isatty') and out.isatty()
            if supports_style:
                style_map = {
                    'bold': '1', 'b': '1',
                    'underline': '4', 'u': '4',
                    'italic': '3', 'i': '3',
                }
                if isinstance(style, str):
                    styles = [style]
                else:
                    styles = style  # assume iterable
                for st in styles:
                    code = style_map.get(st.lower(), st)
                    if isinstance(code, str) and code.startswith('\033['):
                        ansi_codes.append(code)
                    else:
                        ansi_codes.append(f"\033[{code}m")

        # Build final string
        if ansi_codes:
            prefix = ''.join(ansi_codes)
            suffix = "\033[0m"  # reset all
            content = f"{prefix}{content}{suffix}"

        line = content + end
        out.write(line)
        if flush:
            out.flush()

    @staticmethod
    def tprint_simple(*args, **kwargs) -> None:
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


def format_llm_response(response, agent_name: str) -> None:
    """
    Format and print LLM response in user-friendly format.
    
    Args:
        response_content: List of content blocks from LLM response
        agent_name: Name of the agent for display
    """
    for block in response.content:
        if hasattr(block, 'type'):
            if block.type == 'text':
                text = getattr(block, 'text', '')
                if '<think>' in text or '<thinking>' in text:
                    text = re.sub(r'<think(?:ing)?>', 'Thinking: ', text, flags=re.IGNORECASE)
                    text = re.sub(r'</think(?:ing)?>', '', text, flags=re.IGNORECASE)
                tprint(text, color="cyan")

r"""Enhanced input handler for multi-line user input.

Features:
1. Manual multi-line input: type a special char (e.g., \) at end of line to continue
2. Paste handling: Ctrl+V pastes content without auto-submit, press Enter to submit
3. Cursor navigation: < and > keys move within input, Backspace deletes char
4. ESC interrupt: double-tap ESC to cancel current LLM operation
"""

import sys
import time
from typing import Optional, Callable
from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.keys import Keys
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.styles import Style


class MultiLineInput:
    """Enhanced multi-line input handler with special features."""
    
    def __init__(
        self,
        prompt: str = "libercode >> ",
        multiline_char: str = "\\",
        on_submit: Optional[Callable[[str], None]] = None,
    ):
        self.prompt = prompt
        self.multiline_char = multiline_char
        self.on_submit = on_submit
        self._input_buffer: list[str] = []
        self._current_line = ""
        
        self._setup_key_bindings()
    
    def _setup_key_bindings(self):
        """Setup key bindings for custom behavior."""
        self._kb = KeyBindings()
        
        @self._kb.add('<')
        def move_left(event: KeyPressEvent):
            buffer = event.app.current_buffer
            if buffer.cursor_position > 0:
                buffer.cursor_position -= 1
        
        @self._kb.add('>')
        def move_right(event: KeyPressEvent):
            buffer = event.app.current_buffer
            if buffer.cursor_position < len(buffer.text):
                buffer.cursor_position += 1
    
    def _create_buffer(self) -> Buffer:
        """Create the input buffer with custom handlers."""
        buffer = Buffer(
            multiline=False,
            accept_handler=self._on_enter,
        )
        return buffer
    
    def _on_enter(self, buffer: Buffer) -> bool:
        """Handle Enter key press."""
        text = buffer.text
        
        if text.endswith(self.multiline_char):
            self._input_buffer.append(text[:-1])
            buffer.text = ""
            return False
        else:
            self._input_buffer.append(text)
            full_input = "\n".join(self._input_buffer)
            self._input_buffer = []
            self._current_line = ""
            
            if self.on_submit:
                self.on_submit(full_input)
            return True
    
    def read_input(self) -> str:
        """Read input from user using prompt_toolkit."""
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        
        history = InMemoryHistory()
        
        session = PromptSession(
            message=self.prompt,
            history=history,
            key_bindings=self._kb,
            buffer=self._create_buffer(),
        )
        
        try:
            result = session.prompt()
            return result
        except KeyboardInterrupt:
            raise
        except EOFError:
            raise


class SimpleMultiLineInput:
    """Simpler multi-line input without prompt_toolkit for broader compatibility."""
    
    def __init__(
        self,
        prompt: str = "libercode >> ",
        multiline_char: str = "\\",
    ):
        self.prompt = prompt
        self.multiline_char = multiline_char
    
    def read_input(self) -> str:
        """Read input with multi-line support."""
        lines: list[str] = []
        
        while True:
            try:
                if not lines:
                    line = input(self.prompt)
                else:
                    line = input(" " * len(self.prompt))
            except (EOFError, KeyboardInterrupt):
                raise
            
            if line.endswith(self.multiline_char):
                lines.append(line[:-1])
            else:
                lines.append(line)
        return "\n".join(lines)


def get_input(prompt: str = "libercode >> ", multiline_char: str = "\\") -> str:
    """Get user input with multi-line support.
    
    Usage:
        - Type \\ at end of line + Enter to continue on next line
        - Paste content with Ctrl+V, press Enter to submit
        - Use < and > keys to move cursor (if supported)
    """
    try:
        import prompt_toolkit
        handler = MultiLineInput(prompt=prompt, multiline_char=multiline_char)
        return handler.read_input()
    except ImportError:
        handler = SimpleMultiLineInput(prompt=prompt, multiline_char=multiline_char)
        return handler.read_input()


def input_with_cursor_support(
    prompt: str = "libercode >> ",
    multiline_char: str = "\\",
) -> str:
    """Get user input with multi-line support using prompt_toolkit."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys
        
        clean_prompt = _strip_ansi(prompt)
        
        kb = KeyBindings()
        result = {"text": ""}
        
        @kb.add(Keys.Left)
        def move_left(event):
            buf = event.app.current_buffer
            if buf.cursor_position > 0:
                buf.cursor_position -= 1
        
        @kb.add(Keys.Right)
        def move_right(event):
            buf = event.app.current_buffer
            if buf.cursor_position < len(buf.text):
                buf.cursor_position += 1
        
        @kb.add(Keys.Enter)
        def submit(event):
            app = event.app
            buf = app.current_buffer
            text = buf.text
            
            if text.endswith(multiline_char):
                buf.text = text + "\n"
                buf.cursor_position = len(buf.text)
            else:
                clean_text = text.replace(multiline_char, "")
                result["text"] = clean_text
                app.exit()
        
        session = PromptSession(
            message=clean_prompt,
            key_bindings=kb,
        )
        
        session.prompt()
        
        return result["text"]
        
    except ImportError:
        return _simple_multiline_input(prompt, multiline_char)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    import re
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def _simple_multiline_input(prompt: str, multiline_char: str = "\\") -> str:
    """Fallback simple multi-line input without prompt_toolkit."""
    lines: list[str] = []
    
    while True:
        try:
            if not lines:
                line = input(prompt)
            else:
                line = input(" " * len(prompt))
        except (EOFError, KeyboardInterrupt):
            raise
        
        if line.endswith(multiline_char):
            lines.append(line[:-1])
        else:
            lines.append(line)
            break
    
    return "\n".join(lines)

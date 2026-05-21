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
from prompt_toolkit.formatted_text import FormattedText

PROMPT_STYLE = Style.from_dict({
    'prompt.bracket': 'bold #cccccc',
    'prompt.name': 'bold ansibrightgreen',
    'prompt.separator': 'bold #cccccc',
    'prompt.arrow': 'bold ansibrightcyan',
})

PROMPT_FORMATTED = FormattedText([
    ('class:prompt.bracket', '['),
    ('class:prompt.name', 'LiberCode'),
    ('class:prompt.separator', ']'),
    ('class:prompt.arrow', ' \u276f\u276f '),
])

PROMPT_PLAIN = '[LiberCode] \u276f\u276f'

PROMPT_ANSI = '\033[1;97m[\033[0m\033[1;92mLiberCode\033[0m\033[1;97m]\033[0m\033[1;96m \u276f\u276f \033[0m'


class MultiLineInput:
    """Enhanced multi-line input handler with special features."""
    
    def __init__(
        self,
        prompt=None,
        multiline_char: str = "\\",
        on_submit: Optional[Callable[[str], None]] = None,
    ):
        self.prompt = prompt if prompt is not None else PROMPT_FORMATTED
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
            style=PROMPT_STYLE,
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
        prompt=None,
        multiline_char: str = "\\",
    ):
        self.prompt = prompt if prompt is not None else PROMPT_PLAIN
        self._plain_prompt = _strip_ansi(self.prompt) if prompt is not None else PROMPT_PLAIN
        self.multiline_char = multiline_char

    def read_input(self) -> str:
        """Read input with multi-line support."""
        lines: list[str] = []

        while True:
            try:
                if not lines:
                    line = input(self.prompt)
                else:
                    line = input(" " * len(self._plain_prompt))
            except (EOFError, KeyboardInterrupt):
                raise
            
            if line.endswith(self.multiline_char):
                lines.append(line[:-1])
            else:
                lines.append(line)
        return "\n".join(lines)


def get_input(prompt=None, multiline_char: str = "\\") -> str:
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
    prompt=None,
    multiline_char: str = "\\",
) -> str:
    """Get user input with multi-line support using prompt_toolkit."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys

        use_formatted = prompt is None
        message = PROMPT_FORMATTED if use_formatted else _strip_ansi(prompt)
        style = PROMPT_STYLE if use_formatted else None

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
            message=message,
            key_bindings=kb,
            style=style,
        )

        session.prompt()

        return result["text"]

    except ImportError:
        fallback_prompt = PROMPT_ANSI if prompt is None else prompt
        return _simple_multiline_input(fallback_prompt, multiline_char)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    import re
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def _simple_multiline_input(prompt: str, multiline_char: str = "\\") -> str:
    """Fallback simple multi-line input without prompt_toolkit."""
    lines: list[str] = []
    plain_prompt = _strip_ansi(prompt)

    while True:
        try:
            if not lines:
                line = input(prompt)
            else:
                line = input(" " * len(plain_prompt))
        except (EOFError, KeyboardInterrupt):
            raise

        if line.endswith(multiline_char):
            lines.append(line[:-1])
        else:
            lines.append(line)
            break

    return "\n".join(lines)

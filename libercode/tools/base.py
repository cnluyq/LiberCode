"""
Base tools for LiberCode.

Provides foundational tools for bash commands and file operations.
"""
import re
import fnmatch
import subprocess
from pathlib import Path
from typing import Optional, Callable, List
from libercode.exceptions import PathEscapeError, DangerousCommandError

_DEFAULT_DANGEROUS_PATTERNS = [
    "prefix:rm -rf /",
    "prefix:sudo",
    "prefix:shutdown",
    "prefix:reboot",
]

# Module-level config and callback, set by the application at startup.
_config = None
_confirm_callback: Optional[Callable[[str], bool]] = None
_compiled_patterns: Optional[List[tuple]] = None


def _parse_pattern(raw: str) -> tuple:
    """Parse a pattern string into a (type, compiled_matcher, raw) tuple.

    Pattern format: ``[type:]pattern`` where type is one of:
    - ``prefix`` (default) — substring-inclusion match (``pattern in command``)
    - ``glob`` — fnmatch shell-style wildcard match on the full command
    - ``regex`` — regular expression match

    If no type prefix is given, ``prefix`` is assumed for backward
    compatibility with bare patterns like ``sudo``.

    Args:
        raw: Pattern string, optionally prefixed with ``type:``

    Returns:
        Tuple of (pattern_type, matcher, raw_pattern) where matcher is
        a callable that takes a command string and returns bool
    """
    if ":" in raw:
        pat_type, pat_body = raw.split(":", 1)
        pat_type = pat_type.strip()
        pat_body = pat_body.strip()
    else:
        pat_type = "prefix"
        pat_body = raw.strip()

    if pat_type == "prefix":
        return ("prefix", lambda cmd, p=pat_body: p in cmd, raw)
    elif pat_type == "glob":
        return ("glob", lambda cmd, p=pat_body: fnmatch.fnmatch(cmd, p), raw)
    elif pat_type == "regex":
        compiled = re.compile(pat_body)
        return ("regex", lambda cmd, c=compiled: bool(c.search(cmd)), raw)
    else:
        return ("prefix", lambda cmd, p=raw: p in cmd, raw)


def _build_compiled_patterns(patterns: List[str]) -> List[tuple]:
    """Build compiled pattern list from raw pattern strings.

    Args:
        patterns: List of pattern strings to compile

    Returns:
        List of (type, matcher, raw) tuples
    """
    return [_parse_pattern(p) for p in patterns if p.strip()]


def set_dangerous_command_config(
    config,
    confirm_callback: Optional[Callable[[str], bool]] = None,
) -> None:
    """Set the dangerous command policy config and optional confirm callback.

    Also compiles the dangerous command patterns from config for efficient
    matching at runtime.

    Args:
        config: Config instance (must have ``dangerous_command_policy`` and
            ``dangerous_command_patterns`` attributes)
        confirm_callback: Optional callable that takes a command string and
            returns True to allow execution, False to deny. Used when policy
            is "confirm". If not provided, confirm mode falls back to deny.
    """
    global _config, _confirm_callback, _compiled_patterns
    _config = config
    _confirm_callback = confirm_callback
    _compiled_patterns = _build_compiled_patterns(
        getattr(config, "dangerous_command_patterns", _DEFAULT_DANGEROUS_PATTERNS)
    )


def _is_dangerous(command: str) -> bool:
    """Check if a command matches any dangerous pattern.

    Uses the compiled patterns set by :func:`set_dangerous_command_config`.
    Falls back to :data:`_DEFAULT_DANGEROUS_PATTERNS` if config has not
    been initialized.

    Args:
        command: Shell command to check

    Returns:
        True if command matches a dangerous pattern
    """
    patterns = _compiled_patterns
    if patterns is None:
        patterns = _build_compiled_patterns(_DEFAULT_DANGEROUS_PATTERNS)
    return any(matcher(command) for _, matcher, _ in patterns)


def _handle_dangerous_command(command: str) -> None:
    """Handle a dangerous command according to the configured policy.

    Args:
        command: The shell command that was flagged as dangerous

    Raises:
        DangerousCommandError: If policy is "deny" or user rejects in "confirm" mode
    """
    policy = "deny"
    if _config is not None:
        policy = getattr(_config, "dangerous_command_policy", "deny")

    if policy == "allow":
        return

    if policy == "confirm":
        if _confirm_callback is not None:
            allowed = _confirm_callback(command)
            if allowed:
                return
        raise DangerousCommandError(
            f"Blocked dangerous command (confirm unavailable or rejected): {command}"
        )

    # policy == "deny" (default)
    raise DangerousCommandError(f"Blocked dangerous command: {command}")


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

    Dangerous commands are handled according to the configured policy:
    - "deny": Block and raise DangerousCommandError (default)
    - "allow": Execute without asking
    - "confirm": Ask user via callback before executing

    Args:
        command: Shell command to execute
        timeout: Timeout in seconds (default: 120)

    Returns:
        Command output (truncated to 50,000 chars)

    Raises:
        DangerousCommandError: If command is blocked by policy
    """
    if _is_dangerous(command):
        _handle_dangerous_command(command)

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

"""
Custom exception hierarchy for LiberCode.

Provides domain-specific exceptions for clear error handling and debugging.
"""


class LiberCodeError(Exception):
    """Base exception for all LiberCode errors"""
    pass


class ConfigurationError(LiberCodeError):
    """Invalid configuration or missing environment variables"""
    pass


# LLM-related exceptions
class LLMError(LiberCodeError):
    """LLM API errors (rate limits, failures)"""
    pass


class LLMRateLimitError(LLMError):
    """429 rate limit error"""
    pass


class LLMInternalError(LLMError):
    """500 internal error"""
    pass


# Tool execution exceptions
class ToolError(LiberCodeError):
    """Tool execution errors"""
    pass


class DangerousCommandError(ToolError):
    """Dangerous bash command blocked"""
    pass


class PathEscapeError(ToolError):
    """Path traversal attempt blocked"""
    pass


# Task management exceptions
class TaskError(LiberCodeError):
    """Task operation errors"""
    pass


class TaskNotFoundError(TaskError):
    """Task ID does not exist"""
    pass


class TaskClaimError(TaskError):
    """Task cannot be claimed (already owned, blocked)"""
    pass


# Messaging exceptions
class MessageError(LiberCodeError):
    """Message bus errors"""
    pass


# Worktree exceptions
class WorktreeError(LiberCodeError):
    """Worktree operation errors"""
    pass

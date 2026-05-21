"""Logging system for LiberCode.

Provides centralized logging configuration with support for:
- Console logging with colored output
- File logging with rotation
- JSON structured logging for machine parsing
- Per-module log level control
"""

import os
import sys
import json
import logging
import logging.handlers
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class LogConfig:
    """Configuration for logging system."""

    console_level: str = "INFO"
    file_level: str = "DEBUG"

    log_dir: str = ".libercode/logs"
    log_file: str = "libercode.log"
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5

    use_colors: bool = True
    use_json: bool = False
    include_timestamp: bool = True
    include_module: bool = True

    component_levels: Dict[str, str] = None

    def __post_init__(self):
        if self.component_levels is None:
            self.component_levels = {}


class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output."""

    COLORS = {
        'DEBUG': '\033[36m',
        'INFO': '\033[32m',
        'WARNING': '\033[33m',
        'ERROR': '\033[31m',
        'CRITICAL': '\033[35m',
        'RESET': '\033[0m',
    }

    def __init__(self, fmt: str, datefmt: str = None, use_colors: bool = True):
        super().__init__(fmt, datefmt)
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with colors."""
        if self.use_colors and sys.stdout.isatty():
            import copy
            record = copy.copy(record)
            record.levelname = f"{self.COLORS.get(record.levelname, self.COLORS['RESET'])}{record.levelname}{self.COLORS['RESET']}"
            record.msg = f"{self.COLORS.get(record.levelname, self.COLORS['RESET'])}{record.getMessage()}{self.COLORS['RESET']}"

        return super().format(record)


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_obj = {
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }

        if hasattr(record, 'component'):
            log_obj['component'] = record.component
        if hasattr(record, 'teammate'):
            log_obj['teammate'] = record.teammate
        if hasattr(record, 'task_id'):
            log_obj['task_id'] = record.task_id

        if record.exc_info:
            log_obj['exception'] = self.formatException(record.exc_info)

        return json.dumps(log_obj, ensure_ascii=False)


class ContextFilter(logging.Filter):
    """Filter that adds contextual information to log records."""

    def __init__(self, component: str = None, teammate: str = None):
        super().__init__()
        self.component = component
        self.teammate = teammate

    def filter(self, record: logging.LogRecord) -> bool:
        """Add context to record."""
        if self.component:
            record.component = self.component
        if self.teammate:
            record.teammate = self.teammate
        return True


class LiberCodeLogger:
    """Centralized logging system for LiberCode."""

    _instance: Optional['LiberCodeLogger'] = None
    _initialized: bool = False
    _lock: threading.Lock = threading.Lock()

    def __new__(cls, config: LogConfig = None):
        """Singleton pattern for global logger (thread-safe)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config: LogConfig = None):
        """Initialize logging system."""
        if self._initialized:
            return

        self.config = config or LogConfig()
        self._loggers: Dict[str, logging.Logger] = {}
        self._setup_root_logger()
        LiberCodeLogger._initialized = True

    def _setup_root_logger(self) -> None:
        """Configure root logger with handlers."""
        root_logger = logging.getLogger('libercode')
        root_logger.setLevel(logging.DEBUG)

        root_logger.handlers = [h for h in root_logger.handlers
                                if not getattr(h, '_libercode_handler', False)]

        console_handler = self._create_console_handler()
        console_handler._libercode_handler = True
        root_logger.addHandler(console_handler)

        file_handler = self._create_file_handler()
        if file_handler:
            file_handler._libercode_handler = True
            root_logger.addHandler(file_handler)

        root_logger.propagate = False

    def _create_console_handler(self) -> logging.Handler:
        """Create console handler with colored output."""
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(self._get_level(self.config.console_level))

        fmt = "%(levelname)s | %(name)s | %(message)s"
        if self.config.include_timestamp:
            fmt = "%(asctime)s | " + fmt
        datefmt = "%Y-%m-%d %H:%M:%S" if self.config.include_timestamp else None

        formatter = ColoredFormatter(
            fmt, datefmt=datefmt, use_colors=self.config.use_colors
        )
        handler.setFormatter(formatter)

        return handler

    def _create_file_handler(self) -> Optional[logging.Handler]:
        """Create file handler with rotation."""
        try:
            log_path = Path(self.config.log_dir)
            log_path.mkdir(parents=True, exist_ok=True)

            log_file = log_path / self.config.log_file

            handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=self.config.max_bytes,
                backupCount=self.config.backup_count,
                encoding='utf-8'
            )
            handler.setLevel(self._get_level(self.config.file_level))

            if self.config.use_json:
                formatter = JsonFormatter()
            else:
                fmt = "%(asctime)s | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(message)s"
                formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

            handler.setFormatter(formatter)
            return handler

        except Exception as e:
            sys.stderr.write(f"Warning: Could not create log file: {e}\n")
            return None

    def _get_level(self, level_str: str) -> int:
        """Convert level string to logging level."""
        levels = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL,
        }
        return levels.get(level_str.upper(), logging.INFO)

    def get_logger(self, name: str, component: str = None) -> logging.Logger:
        """Get or create a logger for a component."""
        if not name.startswith('libercode'):
            name = f'libercode.{name}'

        if name not in self._loggers:
            logger = logging.getLogger(name)

            if component and component in self.config.component_levels:
                level = self._get_level(self.config.component_levels[component])
                logger.setLevel(level)

            if component:
                context_filter = ContextFilter(component=component)
                logger.addFilter(context_filter)

            self._loggers[name] = logger

        return self._loggers[name]

    def set_level(self, level: str, component: str = None) -> None:
        """Set log level for a component or globally."""
        level_int = self._get_level(level)

        if component:
            logger_name = f'libercode.{component}'
            if logger_name in self._loggers:
                self._loggers[logger_name].setLevel(level_int)
                self.config.component_levels[component] = level
        else:
            self.config.console_level = level
            for handler in logging.getLogger('libercode').handlers:
                if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                    handler.setLevel(level_int)


_global_logger: Optional[LiberCodeLogger] = None


def setup_logging(
    log_dir: str = ".libercode/logs",
    console_level: str = "ERROR",
    file_level: str = "DEBUG",
    use_colors: bool = True,
    use_json: bool = False,
    component_levels: Dict[str, str] = None,
) -> LiberCodeLogger:
    """Setup logging system for LiberCode."""
    global _global_logger

    config = LogConfig(
        log_dir=log_dir,
        console_level=console_level,
        file_level=file_level,
        use_colors=use_colors,
        use_json=use_json,
        component_levels=component_levels or {},
    )

    _global_logger = LiberCodeLogger(config)
    return _global_logger


def get_logger(name: str = None, component: str = None) -> logging.Logger:
    """Get a logger instance."""
    global _global_logger

    if _global_logger is None:
        _global_logger = LiberCodeLogger()

    if name is None:
        import inspect
        frame = inspect.currentframe().f_back
        name = frame.f_globals.get('__name__', 'libercode')

    return _global_logger.get_logger(name, component)


def log_task_event(task_id: int, event: str, details: Dict[str, Any] = None) -> None:
    """Log a task-related event."""
    logger = get_logger('libercode.tasks')
    extra = {'task_id': task_id}
    msg = f"Task #{task_id} - {event}"
    if details:
        msg += f" - {json.dumps(details, ensure_ascii=False)}"
    logger.info(msg, extra=extra)


def log_agent_event(teammate: str, event: str, details: Dict[str, Any] = None) -> None:
    """Log an agent-related event."""
    logger = get_logger('libercode.agents', component='agents')
    extra = {'teammate': teammate}
    msg = f"Agent '{teammate}' - {event}"
    if details:
        msg += f" - {json.dumps(details, ensure_ascii=False)}"
    logger.info(msg, extra=extra)


def log_llm_call(agent: str, model: str, input_tokens: int, output_tokens: int, duration_ms: int) -> None:
    """Log an LLM API call."""
    logger = get_logger('libercode.llm', component='llm')
    logger.info(
        f"LLM call by {agent}: model={model}, "
        f"tokens={input_tokens}in/{output_tokens}out, "
        f"duration={duration_ms}ms"
    )

"""
Configuration management for LiberCode.

Loads configuration from ``libercode.json`` in the project root, falling back
to built-in defaults. API credentials (``LLM_API_KEY``, ``MODEL_ID``,
``LLM_BASE_URL``) are still read from environment variables / ``.env`` since
they should not be committed to version control.
"""

import json
import logging
import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv
from anthropic import AsyncAnthropic

from libercode.exceptions import ConfigurationError

logger = logging.getLogger("libercode.config")


_DEFAULT_DANGEROUS_COMMAND_PATTERNS = [
    "prefix:rm -rf /",
    "prefix:sudo",
    "prefix:shutdown",
    "prefix:reboot",
]

_DEFAULT_CONTEXT_WINDOW = 1_000_000
_DEFAULT_OUTPUT_MAX = 8_192

_DEFAULTS = {
    "debug": False,
    "status_refresh": 5.0,
    "session_auto_save": True,
    "session_auto_save_interval": 1.0,
    "dangerous_command_policy": "confirm",
    "dangerous_command_patterns_override": None,
    "dangerous_command_patterns_extra": [],
    "models": {},
    "default_context_window": _DEFAULT_CONTEXT_WINDOW,
    "default_output_max": _DEFAULT_OUTPUT_MAX,
}


def _load_config_file(workdir: Path) -> dict:
    """Load libercode.json from the project root.

    Returns an empty dict if the file does not exist.

    Args:
        workdir: Project root directory

    Returns:
        Parsed JSON dict

    Raises:
        ConfigurationError: If the file exists but contains invalid JSON
    """
    config_path = workdir / "libercode.json"
    if not config_path.exists():
        logger.debug("No libercode.json found in %s, using defaults", workdir)
        return {}
    logger.info("Loading configuration from %s", config_path)
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigurationError(f"Invalid libercode.json: {e}")


def _resolve_dangerous_command_patterns(file_config: dict) -> list:
    """Resolve the final dangerous command patterns list.

    If ``dangerous_command_patterns_override`` is set (even to an empty list),
    it replaces the defaults entirely. Otherwise, defaults + extra patterns
    are used.

    Args:
        file_config: Parsed libercode.json dict

    Returns:
        Final list of pattern strings
    """
    override = file_config.get("dangerous_command_patterns_override")
    if override is not None:
        return list(override)
    patterns = list(_DEFAULT_DANGEROUS_COMMAND_PATTERNS)
    extra = file_config.get("dangerous_command_patterns_extra", [])
    patterns.extend(extra)
    return patterns


@dataclass
class Config:
    """Configuration container for LiberCode.

    Loads from ``libercode.json`` (project root) plus environment variables
    for API credentials. File values override defaults; missing keys fall
    back to defaults.
    """

    # API Configuration (always from env / .env — never from config file)
    api_key: str
    model_id: str
    base_url: str | None

    # Paths (derived from workdir)
    workdir: Path
    team_dir: Path
    inbox_dir: Path
    tasks_dir: Path

    # Runtime parameters
    poll_interval: int
    idle_timeout: int
    debug: bool

    # Status pane
    status_refresh: float

    # Session auto-save parameters
    session_auto_save: bool
    session_auto_save_interval: float
    session_dir: Path

    # Dangerous command policy: "deny" | "allow" | "confirm"
    dangerous_command_policy: str

    # Dangerous command patterns (resolved from defaults + override/extra)
    dangerous_command_patterns: list

    # Model configurations
    models: dict
    default_context_window: int
    default_output_max: int

    def get_model_config(self, model_id: str) -> tuple[int, int]:
        """Get context_window and output_max for a model.

        Args:
            model_id: Model identifier

        Returns:
            Tuple of (context_window, output_max)
        """
        model_config = self.models.get(model_id, {})
        context = model_config.get("context_window", self.default_context_window)
        output = model_config.get("output_max", self.default_output_max)
        return context, output

    def __init__(self, env_file: str | None = None):
        """Initialize configuration from libercode.json and environment.

        Args:
            env_file: Optional path to .env file (default: loads from cwd/.env)

        Raises:
            ConfigurationError: If required API credentials are missing or
                configuration values are invalid
        """
        load_dotenv(env_file, override=False)

        # API credentials — env only
        self.api_key = self._get_required("LLM_API_KEY")
        self.model_id = self._get_required("MODEL_ID")
        self.base_url = os.getenv("LLM_BASE_URL")

        # Paths
        self.workdir = Path.cwd()
        self.team_dir = self.workdir / ".team"
        self.inbox_dir = self.team_dir / "inbox"
        self.tasks_dir = self.workdir / ".tasks"

        # Load libercode.json
        file_config = _load_config_file(self.workdir)
        logger.debug("Loaded file_config: %s", file_config)

        # Runtime parameters
        self.poll_interval = 5
        self.idle_timeout = 60 * 60 * 12
        self.debug = file_config.get("debug", _DEFAULTS["debug"])
        if not isinstance(self.debug, bool):
            raise ConfigurationError(
                f"libercode.json: 'debug' must be a boolean, got {type(self.debug).__name__}"
            )

        # Status pane
        self.status_refresh = float(
            file_config.get("status_refresh", _DEFAULTS["status_refresh"])
        )

        # Session auto-save
        self.session_auto_save = file_config.get(
            "session_auto_save", _DEFAULTS["session_auto_save"]
        )
        if not isinstance(self.session_auto_save, bool):
            raise ConfigurationError(
                f"libercode.json: 'session_auto_save' must be a boolean, "
                f"got {type(self.session_auto_save).__name__}"
            )
        self.session_auto_save_interval = float(
            file_config.get(
                "session_auto_save_interval",
                _DEFAULTS["session_auto_save_interval"],
            )
        )
        self.session_dir = self.workdir / ".libercode" / "sessions"

        # Dangerous command policy
        policy = file_config.get(
            "dangerous_command_policy", _DEFAULTS["dangerous_command_policy"]
        ).lower()
        if policy not in ("deny", "allow", "confirm"):
            raise ConfigurationError(
                f"libercode.json: 'dangerous_command_policy' must be "
                f"'deny', 'allow', or 'confirm', got {policy!r}"
            )
        self.dangerous_command_policy = policy

        # Dangerous command patterns
        self.dangerous_command_patterns = _resolve_dangerous_command_patterns(
            file_config
        )

        # Model configurations
        self.models = file_config.get("models", _DEFAULTS["models"])
        if not isinstance(self.models, dict):
            raise ConfigurationError(
                f"libercode.json: 'models' must be an object, got {type(self.models).__name__}"
            )
        self.default_context_window = int(
            file_config.get("default_context_window", _DEFAULTS["default_context_window"])
        )
        self.default_output_max = int(
            file_config.get("default_output_max", _DEFAULTS["default_output_max"])
        )

        # Initialize async client for interruptible LLM calls
        self.async_client = AsyncAnthropic(
            api_key=self.api_key, base_url=self.base_url
        )

    def _get_required(self, key: str) -> str:
        """Get required environment variable or raise ConfigurationError"""
        value = os.getenv(key)
        if not value:
            raise ConfigurationError(
                f"Missing required environment variable: {key}"
            )
        return value

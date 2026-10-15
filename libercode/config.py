"""
Configuration management for LiberCode.

Loads configuration from ``libercode.json`` in the project root, falling back
to built-in defaults. API credentials (``LLM_API_KEY``, ``MODEL_ID``,
``LLM_BASE_URL``) are still read from environment variables / ``.env`` since
they should not be committed to version control.

Model configuration follows a 3-tier priority:

1. **libercode.json** (project-level, user-editable) — highest priority
2. **models.json** (shipped with the package, system-level defaults)
3. **Hardcoded defaults** (``_DEFAULT_CONTEXT_WINDOW``, ``_DEFAULT_OUTPUT_MAX``)
"""

import json
import logging
import os
from contextlib import contextmanager
from importlib.resources import files as pkg_files
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
from dotenv import load_dotenv
from anthropic import Anthropic, AsyncAnthropic

from libercode.exceptions import ConfigurationError

logger = logging.getLogger("libercode.config")


@contextmanager
def _isolate_anthropic_env():
    """Temporarily hide ANTHROPIC credential env vars.

    Older anthropic SDK versions (e.g. 0.86.0) unconditionally fall back to
    the ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_API_KEY`` env vars even when
    an explicit ``api_key=`` is passed, which silently authenticates with a
    stale token. Hiding them while constructing clients guarantees the
    explicit key is the only credential used.
    """
    saved = {
        var: os.environ.pop(var)
        for var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
        if var in os.environ
    }
    try:
        yield
    finally:
        os.environ.update(saved)


def build_async_client(api_key: str, base_url: str | None = None) -> AsyncAnthropic:
    """Build an AsyncAnthropic client using only the explicit credentials.

    Args:
        api_key: API key to authenticate with
        base_url: Optional base URL (None uses the SDK default)

    Returns:
        Configured AsyncAnthropic client
    """
    with _isolate_anthropic_env():
        return AsyncAnthropic(api_key=api_key, base_url=base_url)


def build_clients(
    api_key: str, base_url: str | None = None
) -> tuple[Anthropic, AsyncAnthropic]:
    """Build sync and async Anthropic clients using only explicit credentials.

    Args:
        api_key: API key to authenticate with
        base_url: Optional base URL (None uses the SDK default)

    Returns:
        Tuple of (sync client, async client)
    """
    with _isolate_anthropic_env():
        return (
            Anthropic(api_key=api_key, base_url=base_url),
            AsyncAnthropic(api_key=api_key, base_url=base_url),
        )


_DEFAULT_DANGEROUS_COMMAND_PATTERNS = [
    "prefix:rm -rf /",
    "prefix:sudo",
    "prefix:shutdown",
    "prefix:reboot",
]

_DEFAULT_CONTEXT_WINDOW = 256_000
_DEFAULT_OUTPUT_MAX = 8_192

_DEFAULTS = {
    "debug": False,
    "status_refresh": 5.0,
    "session_auto_save": True,
    "session_auto_save_interval": 1.0,
    "poll_interval": 5,
    "idle_timeout": 60*60*12,
    "dangerous_command_policy": "confirm",
    "dangerous_command_patterns_override": None,
    "dangerous_command_patterns_extra": [],
    "dangerous_command_timeout": 1800,
    "teammate_loop_limit": 1000,
    "compact_tool_result_ratio_threshold": 0.6,
    "compact_tool_result_persist_single_content_threshhold": 30000,
    "compact_tool_result_persist_fullsize_threshhold": 200000,
    "compact_history_ratio_threshold": 0.75,
    "compact_history_keep_latest_message_num": 10,
    "compact_history_keep_original_message_num": 10,
    "models": {},
    "default_context_window": _DEFAULT_CONTEXT_WINDOW,
    "default_output_max": _DEFAULT_OUTPUT_MAX,
}


def _load_system_models() -> dict:
    """Load the system-level ``models.json`` shipped with the package.

    Returns:
        Dict mapping model IDs to their ``{"context_window": ..., "output_max": ...}``
        configs, or an empty dict if the file is missing or invalid.
    """
    try:
        models_file = pkg_files("libercode").joinpath("models.json")
        return json.loads(models_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Could not load system models.json: %s", exc)
        return {}


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
    back to defaults. Credentials/model can be switched at runtime via
    :meth:`connect` and :meth:`set_model` (REPL ``/connect`` and ``/model``
    commands), optionally persisted back to ``.env`` via :meth:`persist_env`.
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
    compacts_dir: Path

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

    # Dangerous command confirmation timeout (seconds)
    dangerous_command_timeout: int

    # Teammate loop limit
    teammate_loop_limit: int

    # Context compaction thresholds
    compact_tool_result_persist_single_content_threshhold: int
    compact_tool_result_persist_fullsize_threshhold: int
    compact_history_ratio_threshold: float
    compact_tool_result_ratio_threshold: float
    compact_history_keep_latest_message_num: int
    compact_history_keep_original_message_num: int

    # Model configurations
    models: dict
    default_context_window: int
    default_output_max: int

    # Runtime-switchable LLM clients (managed via connect()/set_model())
    sync_client: Any = field(default=None, init=False, repr=False)
    _env_file: Path = field(default=None, init=False, repr=False)

    def get_model_config(self, model_id: str) -> tuple[int, int]:
        """Get context_window and output_max for a model.

        Resolution order (per-field):

        1. ``libercode.json`` ``models`` entry for *model_id*
        2. System ``models.json`` entry for *model_id*
        3. ``default_context_window`` / ``default_output_max`` (also resolved
           from libercode.json → system defaults → hardcoded fallbacks)

        Args:
            model_id: Model identifier

        Returns:
            Tuple of (context_window, output_max)
        """
        user_cfg = self.models.get(model_id, {})
        system_cfg = self._system_models.get(model_id, {})
        context = user_cfg.get(
            "context_window",
            system_cfg.get("context_window", self.default_context_window),
        )
        output = user_cfg.get(
            "output_max",
            system_cfg.get("output_max", self.default_output_max),
        )
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
        logger.debug(f"init base_url is {self.base_url},  model_id is {self.model_id}")

        # Paths
        self.workdir = Path.cwd()
        self.team_dir = self.workdir / ".team"
        self.inbox_dir = self.team_dir / "inbox"
        self.tasks_dir = self.workdir / ".tasks"
        self.compacts_dir = self.workdir / ".compacts"

        # Load libercode.json
        file_config = _load_config_file(self.workdir)
        logger.debug("Loaded file_config: %s", file_config)

        # Runtime parameters
        poll_interval_val = file_config.get("poll_interval", _DEFAULTS["poll_interval"])
        try:
            self.poll_interval = int(float(poll_interval_val))
        except (TypeError, ValueError):
            self.poll_interval = _DEFAULTS["poll_interval"]

        idle_timeout_val = file_config.get("idle_timeout", _DEFAULTS["idle_timeout"])
        try:
            self.idle_timeout = int(float(idle_timeout_val))
        except (TypeError, ValueError):
            self.idle_timeout = _DEFAULTS["poll_interval"]

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

        # Dangerous command confirmation timeout
        timeout_val = file_config.get(
            "dangerous_command_timeout", _DEFAULTS["dangerous_command_timeout"]
        )
        try:
            self.dangerous_command_timeout = int(float(timeout_val))
        except (TypeError, ValueError):
            self.dangerous_command_timeout = _DEFAULTS["dangerous_command_timeout"]
        if self.dangerous_command_timeout < 1:
            raise ConfigurationError(
                f"libercode.json: 'dangerous_command_timeout' must be >= 1, "
                f"got {self.dangerous_command_timeout}"
            )

        # Teammate loop limit
        teammate_loop_limit_val = file_config.get("teammate_loop_limit", _DEFAULTS["teammate_loop_limit"])
        try:
            self.teammate_loop_limit = int(teammate_loop_limit_val)
        except (TypeError, ValueError):
            self.teammate_loop_limit = _DEFAULTS["teammate_loop_limit"]
        if self.teammate_loop_limit < 1:
            raise ConfigurationError(
                f"libercode.json: 'teammate_loop_limit' must be >= 1, got {self.teammate_loop_limit}"
            )

        # Context compaction thresholds
        self.compact_tool_result_persist_single_content_threshhold = int(
            file_config.get(
                "compact_tool_result_persist_single_content_threshhold",
                _DEFAULTS["compact_tool_result_persist_single_content_threshhold"],
            )
        )
        self.compact_tool_result_persist_fullsize_threshhold = int(
            file_config.get(
                "compact_tool_result_persist_fullsize_threshhold",
                _DEFAULTS["compact_tool_result_persist_fullsize_threshhold"],
            )
        )
        ratio = file_config.get(
            "compact_history_ratio_threshold",
            _DEFAULTS["compact_history_ratio_threshold"],
        )
        self.compact_history_ratio_threshold = float(ratio)
        if not (0 < self.compact_history_ratio_threshold <= 1.0):
            raise ConfigurationError(
                f"libercode.json: 'compact_history_ratio_threshold' must be between 0 and 1, "
                f"got {self.compact_history_ratio_threshold}"
            )
        tr_ratio = file_config.get(
            "compact_tool_result_ratio_threshold",
            _DEFAULTS["compact_tool_result_ratio_threshold"],
        )
        self.compact_tool_result_ratio_threshold = float(tr_ratio)
        if not (0 < self.compact_tool_result_ratio_threshold <= 1.0):
            raise ConfigurationError(
                f"libercode.json: 'compact_tool_result_ratio_threshold' must be between 0 and 1, "
                f"got {self.compact_tool_result_ratio_threshold}"
            )
        self.compact_history_keep_latest_message_num = int(
            file_config.get(
                "compact_history_keep_latest_message_num",
                _DEFAULTS["compact_history_keep_latest_message_num"],
            )
        )
        if self.compact_history_keep_latest_message_num < 0:
            raise ConfigurationError(
                f"libercode.json: 'compact_history_keep_latest_message_num' must be >= 0, "
                f"got {self.compact_history_keep_latest_message_num}"
            )
        self.compact_history_keep_original_message_num = int(
            file_config.get(
                "compact_history_keep_original_message_num",
                _DEFAULTS["compact_history_keep_original_message_num"],
            )
        )
        if self.compact_history_keep_original_message_num < 0:
            raise ConfigurationError(
                f"libercode.json: 'compact_history_keep_original_message_num' must be >= 0, "
                f"got {self.compact_history_keep_original_message_num}"
            )

        # Model configurations
        self._system_models = _load_system_models()
        logger.debug("Loaded system_models: %d entries", len(self._system_models))

        self.models = file_config.get("models", _DEFAULTS["models"])
        if not isinstance(self.models, dict):
            raise ConfigurationError(
                f"libercode.json: 'models' must be an object, got {type(self.models).__name__}"
            )
        self.default_context_window = int(_DEFAULTS["default_context_window"])
        self.default_output_max = int(_DEFAULTS["default_output_max"])

        # Initialize sync + async clients for LLM calls
        self.sync_client, self.async_client = build_clients(
            self.api_key, self.base_url
        )

        # Track .env file path for runtime persistence of /connect and /model
        self._env_file = Path(env_file) if env_file else self.workdir / ".env"

    def connect(self, api_key: str, base_url: str | None = None) -> None:
        """Switch the LLM provider at runtime.

        Recreates both sync and async clients with the new credentials.
        All agents read these clients dynamically, so the change takes
        effect on their next LLM call.

        Args:
            api_key: New API key
            base_url: Optional new base URL (None keeps Anthropic default)

        Raises:
            ConfigurationError: If api_key is empty
        """
        if not api_key:
            raise ConfigurationError("API key cannot be empty")
        self.api_key = api_key
        self.base_url = base_url or None
        self.sync_client, self.async_client = build_clients(
            self.api_key, self.base_url
        )
        logger.info(
            f"LLM provider switched: base_url={self.base_url}, model_id={self.model_id}"
        )

    def set_model(self, model_id: str) -> None:
        """Switch the model at runtime.

        Model id is read dynamically from ``config.model_id`` on every LLM
        call, so this takes effect for lead and all teammates immediately.

        Args:
            model_id: New model identifier

        Raises:
            ConfigurationError: If model_id is empty
        """
        if not model_id:
            raise ConfigurationError("Model ID cannot be empty")
        self.model_id = model_id
        logger.info(f"Model switched to: {self.model_id}")

    def persist_env(self) -> None:
        """Persist current credentials/model to the ``.env`` file.

        Writes ``LLM_API_KEY``, ``MODEL_ID`` and ``LLM_BASE_URL`` (when set)
        so the configuration survives a restart. Also updates ``os.environ``.
        """
        from dotenv import set_key

        env_path = str(self._env_file)
        set_key(env_path, "LLM_API_KEY", self.api_key, quote_mode="auto")
        set_key(env_path, "MODEL_ID", self.model_id, quote_mode="auto")
        if self.base_url:
            set_key(env_path, "LLM_BASE_URL", self.base_url, quote_mode="auto")
        os.environ["LLM_API_KEY"] = self.api_key
        os.environ["MODEL_ID"] = self.model_id
        if self.base_url:
            os.environ["LLM_BASE_URL"] = self.base_url
        logger.info(f"Persisted LLM connection settings to {env_path}")

    def _get_required(self, key: str) -> str:
        """Get required environment variable or raise ConfigurationError"""
        value = os.getenv(key)
        if not value:
            raise ConfigurationError(
                f"Missing required environment variable: {key}"
            )
        return value

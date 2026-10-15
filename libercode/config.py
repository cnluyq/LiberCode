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
import time
from contextlib import contextmanager
from importlib.resources import files as pkg_files
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
from dotenv import load_dotenv
from anthropic import Anthropic, AsyncAnthropic

from libercode.exceptions import ConfigurationError

logger = logging.getLogger("libercode.config")

_MODELS_DEV_URL = "https://models.dev/api.json"
_MODELS_DEV_TTL_SECONDS = 24 * 60 * 60


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
    _fetched_pricing: dict = field(default_factory=dict, init=False, repr=False)
    _pricing_sources: dict = field(default_factory=dict, init=False, repr=False)
    _fetched_limits: dict = field(default_factory=dict, init=False, repr=False)
    _model_limit_sources: dict = field(default_factory=dict, init=False, repr=False)

    def get_model_config(self, model_id: str) -> tuple[int, int]:
        """Get context_window and output_max for a model.

        Resolution order (per-field):

        1. ``libercode.json`` ``models`` entry for *model_id*
        2. System ``models.json`` entry for *model_id*
        3. Limits auto-fetched from the provider / models.dev
        4. ``default_context_window`` / ``default_output_max`` (also resolved
           from libercode.json → system defaults → hardcoded fallbacks)

        Args:
            model_id: Model identifier

        Returns:
            Tuple of (context_window, output_max)
        """
        user_cfg = self.models.get(model_id, {})
        system_cfg = self._system_models.get(model_id, {})
        fetched = self._fetched_limits.get(model_id, {})
        context = user_cfg.get(
            "context_window",
            system_cfg.get(
                "context_window",
                fetched.get("context_window", self.default_context_window),
            ),
        )
        output = user_cfg.get(
            "output_max",
            system_cfg.get("output_max", fetched.get("output_max", self.default_output_max)),
        )
        return context, output

    def get_model_config_source(self, model_id: str) -> str | None:
        """Get the source of the model limits (context_window/output_max).

        Args:
            model_id: Model identifier

        Returns:
            One of "libercode.json", "models.json", "provider",
            "models.dev", or None when falling back to hardcoded defaults
        """
        user_cfg = self.models.get(model_id, {})
        if "context_window" in user_cfg or "output_max" in user_cfg:
            return "libercode.json"
        system_cfg = self._system_models.get(model_id, {})
        if "context_window" in system_cfg or "output_max" in system_cfg:
            return "models.json"
        if model_id in self._fetched_limits:
            return self._model_limit_sources.get(model_id, "provider")
        return None

    def get_model_pricing(self, model_id: str) -> dict | None:
        """Get pricing (USD per 1M tokens) for a model.

        Resolution order:

        1. ``libercode.json`` ``models`` entry ``pricing`` for *model_id*
        2. System ``models.json`` entry ``pricing`` for *model_id*
        3. Pricing fetched automatically — provider model list endpoint
           first, then the models.dev community catalog (see
           :meth:`refresh_pricing`)

        Args:
            model_id: Model identifier

        Returns:
            Pricing dict with optional keys ``input``, ``output``,
            ``cache_read``, ``cache_creation``, or None if not configured
        """
        user_cfg = self.models.get(model_id, {})
        pricing = user_cfg.get("pricing")
        if pricing is None:
            pricing = self._system_models.get(model_id, {}).get("pricing")
        if pricing is None:
            pricing = self._fetched_pricing.get(model_id)
        return pricing if isinstance(pricing, dict) else None

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

        # Auto-fetched pricing caches (provider + models.dev)
        self._fetched_pricing = {}
        self._pricing_sources = {}
        self._fetched_limits = {}
        self._model_limit_sources = {}

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
        self._fetched_pricing = {}
        self._pricing_sources = {}
        self._fetched_limits = {}
        self._model_limit_sources = {}
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

    def get_model_pricing_source(self, model_id: str) -> str | None:
        """Get the source of the pricing used for a model.

        Args:
            model_id: Model identifier

        Returns:
            One of "libercode.json", "models.json", "provider",
            "models.dev", or None if no pricing is available
        """
        user_cfg = self.models.get(model_id, {})
        if isinstance(user_cfg.get("pricing"), dict):
            return "libercode.json"
        if isinstance(self._system_models.get(model_id, {}).get("pricing"), dict):
            return "models.json"
        if model_id in self._fetched_pricing:
            return self._pricing_sources.get(model_id, "provider")
        return None

    async def fetch_model_info(self, timeout: float = 10.0) -> list[dict]:
        """Fetch raw model entries from the currently connected provider.

        Probes a few candidate model-list endpoints (Anthropic-style and
        OpenAI-compatible) using the current credentials.

        Args:
            timeout: Per-request HTTP timeout in seconds

        Returns:
            List of raw model entry dicts, or an empty list if the
            provider does not expose a model list
        """
        import httpx

        key = self.api_key
        base = (self.base_url or "https://api.anthropic.com").rstrip("/")
        candidates = [
            (
                f"{base}/v1/models",
                {
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "model-list-2025-06-01",
                },
            ),
        ]
        if base.endswith("/anthropic"):
            root = base[: -len("/anthropic")]
            candidates.extend(
                [
                    (f"{root}/models", {"Authorization": f"Bearer {key}"}),
                    (f"{root}/v1/models", {"Authorization": f"Bearer {key}"}),
                ]
            )
        candidates.append((f"{base}/models", {"Authorization": f"Bearer {key}"}))

        for url, headers in candidates:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    entries = self._parse_model_entries(resp.json())
                    if entries:
                        logger.info(f"Fetched {len(entries)} models from {url}")
                        return entries
                logger.debug(
                    f"Model list endpoint {url} returned status {resp.status_code}"
                )
            except Exception as e:
                logger.debug(f"Model list endpoint {url} failed: {e}")
                continue
        logger.warning("Could not fetch model list from any candidate endpoint")
        return []

    async def fetch_available_models(self, timeout: float = 10.0) -> list[str]:
        """Fetch the model IDs available on the currently connected provider.

        Also refreshes the auto-fetched pricing and model limits caches from
        any data exposed by the provider's model list.

        Args:
            timeout: Per-request HTTP timeout in seconds

        Returns:
            Sorted list of model IDs, or an empty list if the provider
            does not expose a model list
        """
        entries = await self.fetch_model_info(timeout=timeout)
        self.update_fetched_model_info(entries)
        return sorted({e["id"] for e in entries if e.get("id")})

    async def refresh_pricing(self, timeout: float = 10.0) -> bool:
        """Fetch model pricing and limits from the connected provider and models.dev.

        Runs silently — failures are logged at debug level and simply leave
        the caches empty, in which case cost displays show N/A and context
        windows show "unknown".

        Sources (highest priority first):
        1. The connected provider's model list endpoint (if it exposes pricing)
        2. The community models.dev catalog

        Args:
            timeout: Per-request HTTP timeout in seconds

        Returns:
            True if pricing data was obtained for at least one model
        """
        try:
            entries = await self.fetch_model_info(timeout=timeout)
        except Exception as e:
            logger.debug(f"Provider pricing refresh failed: {e}")
            entries = []
        self.update_fetched_model_info(entries)
        dev_by_provider = await self.fetch_models_dev_info(timeout=timeout)
        if dev_by_provider:
            dev_pricing = self.select_models_dev_pricing(dev_by_provider)
            for model_id, pricing in dev_pricing.items():
                if model_id not in self._fetched_pricing:
                    self._fetched_pricing[model_id] = pricing
                    self._pricing_sources[model_id] = "models.dev"
            dev_limits = self.select_models_dev_limits(dev_by_provider)
            for model_id, limits in dev_limits.items():
                if model_id not in self._fetched_limits:
                    self._fetched_limits[model_id] = limits
                    self._model_limit_sources[model_id] = "models.dev"
        return bool(self._fetched_pricing)

    async def fetch_models_dev_info(self, timeout: float = 15.0) -> dict:
        """Fetch the models.dev community catalog (pricing + model limits).

        Results are cached to ``~/.cache/libercode/models-dev.json`` for 24h;
        a stale cache is used as fallback when the network fetch fails.

        Args:
            timeout: Per-request HTTP timeout in seconds

        Returns:
            Dict mapping provider IDs to ``{model_id: {"pricing": ..., "limits": ...}}``
        """
        import httpx

        cache_path = Path.home() / ".cache" / "libercode" / "models-dev.json"
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if 0 <= age < _MODELS_DEV_TTL_SECONDS:
                try:
                    return self._build_models_dev_info_map(
                        json.loads(cache_path.read_text(encoding="utf-8"))
                    )
                except Exception as e:
                    logger.debug(f"Failed to read models.dev cache: {e}")

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(_MODELS_DEV_URL)
            if resp.status_code == 200:
                text = resp.text
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(text, encoding="utf-8")
                except Exception as e:
                    logger.debug(f"Failed to cache models.dev data: {e}")
                return self._build_models_dev_info_map(json.loads(text))
        except Exception as e:
            logger.debug(f"models.dev fetch failed: {e}")

        if cache_path.exists():
            try:
                return self._build_models_dev_info_map(
                    json.loads(cache_path.read_text(encoding="utf-8"))
                )
            except Exception as e:
                logger.debug(f"Failed to read stale models.dev cache: {e}")
        return {}

    def select_models_dev_pricing(self, by_provider: dict) -> dict:
        """Flatten per-provider model info into a model-id pricing map.

        The provider matching the connected ``base_url`` host wins for
        models it lists; remaining models are filled from other providers
        (first seen wins).

        Args:
            by_provider: ``{provider_id: {model_id: info}}`` map

        Returns:
            Flat ``{model_id: pricing}`` dict
        """
        merged: dict = {}
        provider_id = self._guess_provider_id()
        if provider_id and provider_id in by_provider:
            for model_id, info in by_provider[provider_id].items():
                if isinstance(info, dict) and isinstance(info.get("pricing"), dict):
                    merged[model_id] = info["pricing"]
        for pid, pmap in by_provider.items():
            if pid == provider_id:
                continue
            for model_id, info in pmap.items():
                if isinstance(info, dict) and isinstance(info.get("pricing"), dict):
                    merged.setdefault(model_id, info["pricing"])
        return merged

    def select_models_dev_limits(self, by_provider: dict) -> dict:
        """Flatten per-provider model info into a model-id limits map.

        Args:
            by_provider: ``{provider_id: {model_id: info}}`` map

        Returns:
            Flat ``{model_id: {"context_window": int, "output_max": int}}`` dict
        """
        merged: dict = {}
        provider_id = self._guess_provider_id()
        if provider_id and provider_id in by_provider:
            for model_id, info in by_provider[provider_id].items():
                if isinstance(info, dict) and isinstance(info.get("limits"), dict):
                    merged[model_id] = info["limits"]
        for pid, pmap in by_provider.items():
            if pid == provider_id:
                continue
            for model_id, info in pmap.items():
                if isinstance(info, dict) and isinstance(info.get("limits"), dict):
                    merged.setdefault(model_id, info["limits"])
        return merged

    def _guess_provider_id(self) -> str | None:
        """Guess the models.dev provider ID from the connected base URL."""
        if not self.base_url:
            return "anthropic"
        try:
            from urllib.parse import urlparse

            host = (urlparse(self.base_url).hostname or "").lower()
        except Exception:
            return None
        return {
            "api.deepseek.com": "deepseek",
            "api.anthropic.com": "anthropic",
            "api.openai.com": "openai",
            "openrouter.ai": "openrouter",
            "api.moonshot.cn": "moonshotai",
            "api.stepfun.com": "stepfun",
            "api.minimax.io": "minimax",
            "api.minimaxi.com": "minimax",
            "api.z.ai": "zai",
            "dashscope.aliyuncs.com": "dashscope",
            "api.mistral.ai": "mistral",
            "api.x.ai": "xai",
            "generativelanguage.googleapis.com": "google",
            "api.groq.com": "groq",
            "api.cerebras.ai": "cerebras",
            "api.together.xyz": "together",
            "api.perplexity.ai": "perplexity",
            "api.siliconflow.cn": "siliconflow",
        }.get(host)

    @staticmethod
    def _build_models_dev_info_map(data: Any) -> dict:
        """Build a per-provider model info map from a models.dev catalog.

        Each model entry yields ``{"pricing": {...}, "limits": {...}}``.
        Pricing comes from the ``cost`` dict (USD per 1M tokens; ``cache_write``
        is mapped to ``cache_creation``; ``reasoning`` is ignored). Limits come
        from the ``limit`` dict (``context`` → context_window, ``output`` →
        output_max).
        """
        by_provider: dict = {}
        if not isinstance(data, dict):
            return by_provider
        for provider_id, provider in data.items():
            if not isinstance(provider, dict):
                continue
            models = provider.get("models")
            if not isinstance(models, dict):
                continue
            pmap: dict = {}
            for model_id, entry in models.items():
                if not isinstance(entry, dict):
                    continue
                info: dict = {}
                cost = entry.get("cost")
                if isinstance(cost, dict):
                    try:
                        p_input = float(cost["input"])
                        p_output = float(cost["output"])
                    except (KeyError, TypeError, ValueError):
                        p_input = p_output = None
                    if p_input is not None:
                        pricing = {"input": p_input, "output": p_output}
                        if cost.get("cache_read") is not None:
                            try:
                                pricing["cache_read"] = float(cost["cache_read"])
                            except (TypeError, ValueError):
                                pass
                        if cost.get("cache_write") is not None:
                            try:
                                pricing["cache_creation"] = float(cost["cache_write"])
                            except (TypeError, ValueError):
                                pass
                        info["pricing"] = pricing
                limit = entry.get("limit")
                if isinstance(limit, dict):
                    limits = Config._extract_model_limits(
                        {"context_window": limit.get("context"), "max_output_tokens": limit.get("output")}
                    )
                    if limits:
                        info["limits"] = limits
                if info:
                    pmap[model_id] = info
            if pmap:
                by_provider[provider_id] = pmap
        return by_provider

    def update_fetched_model_info(self, entries: list[dict]) -> None:
        """Rebuild the fetched pricing and limits caches from raw model entries.

        Args:
            entries: List of raw model entry dicts from the provider
        """
        fetched_pricing: dict = {}
        fetched_limits: dict = {}
        for entry in entries:
            model_id = entry.get("id") if isinstance(entry, dict) else None
            if not model_id:
                continue
            pricing = self._extract_pricing(entry)
            if pricing:
                fetched_pricing[model_id] = pricing
            limits = self._extract_model_limits(entry)
            if limits:
                fetched_limits[model_id] = limits
        # Preserve models.dev data for models the provider does not cover
        dev_pricing_entries = {
            mid: self._fetched_pricing[mid]
            for mid, src in self._pricing_sources.items()
            if src == "models.dev" and mid in self._fetched_pricing
        }
        dev_limit_entries = {
            mid: self._fetched_limits[mid]
            for mid, src in self._model_limit_sources.items()
            if src == "models.dev" and mid in self._fetched_limits
        }
        self._fetched_pricing = {**dev_pricing_entries, **fetched_pricing}
        self._pricing_sources = {
            **{mid: "models.dev" for mid in dev_pricing_entries},
            **{mid: "provider" for mid in fetched_pricing},
        }
        self._fetched_limits = {**dev_limit_entries, **fetched_limits}
        self._model_limit_sources = {
            **{mid: "models.dev" for mid in dev_limit_entries},
            **{mid: "provider" for mid in fetched_limits},
        }
        if fetched_pricing:
            logger.info(
                f"Obtained pricing for {len(fetched_pricing)} models from provider"
            )
        if fetched_limits:
            logger.info(
                f"Obtained limits for {len(fetched_limits)} models from provider"
            )

    @staticmethod
    def _extract_model_limits(entry: dict) -> dict | None:
        """Extract context/output limits from a raw model entry.

        Recognizes ``context_window`` / ``context_length`` and
        ``max_output_tokens`` / ``max_completion_tokens``.

        Args:
            entry: Raw model entry dict

        Returns:
            Dict with optional ``context_window`` and ``output_max`` ints,
            or None if no limits are exposed
        """
        limits: dict = {}
        context = entry.get("context_window")
        if context is None:
            context = entry.get("context_length")
        if context is not None:
            try:
                limits["context_window"] = int(float(context))
            except (TypeError, ValueError):
                pass
        output = entry.get("max_output_tokens")
        if output is None:
            output = entry.get("max_completion_tokens")
        if output is not None:
            try:
                limits["output_max"] = int(float(output))
            except (TypeError, ValueError):
                pass
        return limits or None

    @staticmethod
    def _extract_pricing(entry: dict) -> dict | None:
        """Normalize provider-specific pricing into a per-1M-token USD dict.

        Recognizes:

        - OpenRouter style: ``pricing.prompt`` / ``pricing.completion``
          (per-token USD)
        - Per-1M convention: ``pricing.input`` / ``pricing.output``
        - LiteLLM style: ``input_cost_per_token`` / ``output_cost_per_token``
          (per-token USD)

        Args:
            entry: Raw model entry dict

        Returns:
            Pricing dict with optional ``input``, ``output``, ``cache_read``,
            ``cache_creation`` keys, or None if no pricing is exposed
        """
        pricing = entry.get("pricing")
        if isinstance(pricing, dict):
            prompt = pricing.get("prompt")
            completion = pricing.get("completion")
            if prompt is not None and completion is not None:
                try:
                    return {
                        "input": float(prompt) * 1_000_000,
                        "output": float(completion) * 1_000_000,
                    }
                except (TypeError, ValueError):
                    return None
            p_in = pricing.get("input")
            p_out = pricing.get("output")
            if p_in is not None and p_out is not None:
                try:
                    result = {"input": float(p_in), "output": float(p_out)}
                except (TypeError, ValueError):
                    return None
                for key in ("cache_read", "cache_creation"):
                    value = pricing.get(key)
                    if value is not None:
                        try:
                            result[key] = float(value)
                        except (TypeError, ValueError):
                            pass
                return result
        in_per_token = entry.get("input_cost_per_token")
        out_per_token = entry.get("output_cost_per_token")
        if in_per_token is not None and out_per_token is not None:
            try:
                return {
                    "input": float(in_per_token) * 1_000_000,
                    "output": float(out_per_token) * 1_000_000,
                }
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _parse_model_entries(data: Any) -> list[dict]:
        """Extract model entry dicts from an Anthropic- or OpenAI-style payload."""
        if isinstance(data, dict):
            for field in ("data", "models"):
                items = data.get(field)
                if isinstance(items, list):
                    entries = [item for item in items if isinstance(item, dict)]
                    if entries:
                        return entries
        return []

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

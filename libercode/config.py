"""
Configuration management for LiberCode.

Loads and validates environment variables, provides paths and runtime parameters.
"""

import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

from libercode.exceptions import ConfigurationError


@dataclass
class Config:
    """
    Configuration container for LiberCode.

    Loads from environment variables and provides validated configuration.
    """

    # API Configuration
    api_key: str
    model_id: str
    base_url: str | None

    # Paths
    workdir: Path
    team_dir: Path
    inbox_dir: Path
    tasks_dir: Path

    # Runtime parameters
    poll_interval: int
    idle_timeout: int

    def __init__(self, env_file: str | None = None):
        """
        Initialize configuration from environment.

        Args:
            env_file: Optional path to .env file (default: loads from cwd/.env)
        """
        # Load environment
        # Don't override existing env vars (important for testing)
        load_dotenv(env_file, override=False)

        # Validate required fields
        self.api_key = self._get_required("LLM_API_KEY")
        self.model_id = self._get_required("MODEL_ID")
        self.base_url = os.getenv("LLM_BASE_URL")

        # Setup paths
        self.workdir = Path.cwd()
        self.team_dir = self.workdir / ".team"
        self.inbox_dir = self.team_dir / "inbox"
        self.tasks_dir = self.workdir / ".tasks"

        # Runtime parameters
        self.poll_interval = 5  # seconds
        self.idle_timeout = 60  # seconds

        # Handle base_url side effect from original code
        if self.base_url:
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

    def _get_required(self, key: str) -> str:
        """Get required environment variable or raise ConfigurationError"""
        value = os.getenv(key)
        if not value:
            raise ConfigurationError(f"Missing required environment variable: {key}")
        return value

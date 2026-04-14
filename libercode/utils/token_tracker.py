"""
Token usage tracking for LiberCode.

Tracks cumulative token usage across LLM calls for monitoring and reporting.
"""

from typing import Dict
from dataclasses import dataclass, field


@dataclass
class TokenTracker:
    """
    Singleton class to track token usage across all LLM calls.

    Tracks per-model statistics:
    - input_tokens
    - output_tokens
    - cache_creation_input_tokens
    - cache_read_input_tokens
    """

    _stats: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def update(self, response) -> None:
        """
        Update token statistics from an Anthropic API response.

        Args:
            response: Anthropic Message response object with usage attribute
        """
        model = response.model
        usage = response.usage

        # Initialize model entry if needed
        if model not in self._stats:
            self._stats[model] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }

        # Accumulate stats (treat None as 0)
        self._stats[model]["input_tokens"] += usage.input_tokens or 0
        self._stats[model]["output_tokens"] += usage.output_tokens or 0
        self._stats[model]["cache_creation_input_tokens"] += (
            usage.cache_creation_input_tokens or 0
        )
        self._stats[model]["cache_read_input_tokens"] += usage.cache_read_input_tokens or 0

    def get_stats(self) -> Dict[str, Dict[str, int]]:
        """
        Get current token statistics.

        Returns:
            Dict mapping model names to token counts
        """
        return self._stats.copy()

    def format_summary(self) -> str:
        """
        Format token statistics as human-readable summary.

        Returns:
            Multi-line string with formatted statistics
        """
        lines = ["=== Token Usage Statistics (total from session start) ==="]
        for model, stats in self._stats.items():
            lines.append(f"Model: {model}")
            lines.append(f"  Input tokens: {stats['input_tokens']}")
            lines.append(f"  Output tokens: {stats['output_tokens']}")
            lines.append(f"  Cache creation input tokens: {stats['cache_creation_input_tokens']}")
            lines.append(f"  Cache read input tokens: {stats['cache_read_input_tokens']}")

        return "\n".join(lines)

"""Token usage tracking for LiberCode.

Tracks detailed token usage across LLM calls for monitoring and reporting.
Supports per-call records with caller info, timestamp, duration, and various output formats.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import argparse


@dataclass
class TokenRecord:
    """Single LLM call token record."""
    caller: str
    timestamp: datetime
    duration_ms: int
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


class TokenTracker:
    """Singleton class to track token usage across all LLM calls.

    Maintains detailed per-call records with caller info, timestamp, duration.
    Supports various output formats via command-line arguments.
    """

    _instance: Optional["TokenTracker"] = None
    _records: List[TokenRecord] = field(default_factory=list)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._records = []
        return cls._instance

    def record(
        self,
        caller: str,
        response,
        duration_ms: int,
    ) -> None:
        """Record a single LLM call with full details.

        Args:
            caller: Name of the caller (lead or teammate name)
            response: Anthropic Message response object with usage attribute
            duration_ms: Execution time in milliseconds
        """
        usage = response.usage
        record = TokenRecord(
            caller=caller,
            timestamp=datetime.now(),
            duration_ms=duration_ms,
            model=response.model,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
        )
        self._records.append(record)

    def get_records(self) -> List[TokenRecord]:
        """Get all token records."""
        return self._records.copy()

    def get_records_by_caller(self, caller: str) -> List[TokenRecord]:
        """Get all records for a specific caller."""
        return [r for r in self._records if r.caller == caller]

    def get_records_by_model(self, model: str) -> List[TokenRecord]:
        """Get all records for a specific model."""
        return [r for r in self._records if r.model == model]

    def get_caller_summary(self) -> Dict[str, Dict[str, int]]:
        """Get aggregated summary grouped by caller.

        Returns:
            Dict mapping caller names to their aggregated token stats
        """
        summary: Dict[str, Dict[str, int]] = {}
        for record in self._records:
            if record.caller not in summary:
                summary[record.caller] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "total_tokens": 0,
                    "call_count": 0,
                    "total_duration_ms": 0,
                }
            summary[record.caller]["input_tokens"] += record.input_tokens
            summary[record.caller]["output_tokens"] += record.output_tokens
            summary[record.caller]["cache_creation_input_tokens"] += record.cache_creation_input_tokens
            summary[record.caller]["cache_read_input_tokens"] += record.cache_read_input_tokens
            summary[record.caller]["total_tokens"] += record.total_tokens
            summary[record.caller]["call_count"] += 1
            summary[record.caller]["total_duration_ms"] += record.duration_ms
        return summary

    def get_model_summary(self) -> Dict[str, Dict[str, int]]:
        """Get aggregated summary grouped by model.

        Returns:
            Dict mapping model names to their aggregated token stats
        """
        summary: Dict[str, Dict[str, int]] = {}
        for record in self._records:
            if record.model not in summary:
                summary[record.model] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "total_tokens": 0,
                    "call_count": 0,
                    "total_duration_ms": 0,
                }
            summary[record.model]["input_tokens"] += record.input_tokens
            summary[record.model]["output_tokens"] += record.output_tokens
            summary[record.model]["cache_creation_input_tokens"] += record.cache_creation_input_tokens
            summary[record.model]["cache_read_input_tokens"] += record.cache_read_input_tokens
            summary[record.model]["total_tokens"] += record.total_tokens
            summary[record.model]["call_count"] += 1
            summary[record.model]["total_duration_ms"] += record.duration_ms
        return summary

    def get_total_summary(self) -> Dict[str, int]:
        """Get total summary across all callers.

        Returns:
            Dict with total token stats
        """
        return {
            "input_tokens": sum(r.input_tokens for r in self._records),
            "output_tokens": sum(r.output_tokens for r in self._records),
            "cache_creation_input_tokens": sum(
                r.cache_creation_input_tokens for r in self._records
            ),
            "cache_read_input_tokens": sum(
                r.cache_read_input_tokens for r in self._records
            ),
            "total_tokens": sum(r.total_tokens for r in self._records),
            "call_count": len(self._records),
            "total_duration_ms": sum(r.duration_ms for r in self._records),
        }

    def format_records(self, records: List[TokenRecord]) -> str:
        """Format a list of token records as human-readable text.

        Args:
            records: List of TokenRecord to format

        Returns:
            Formatted multi-line string
        """
        if not records:
            return "No records found."
        lines = []
        for i, r in enumerate(records, 1):
            lines.append(
                f"{i}. [{r.caller}] {r.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            lines.append(f" Model: {r.model}")
            lines.append(f" Tokens: {r.input_tokens}in / {r.output_tokens}out")
            lines.append(f" Cache: {r.cache_read_input_tokens}read / {r.cache_creation_input_tokens}create")
            lines.append(f" Total: {r.total_tokens} | Duration: {r.duration_ms}ms")
        return "\n".join(lines)

    def format_caller_summary(self, summary: Dict[str, Dict[str, int]]) -> str:
        """Format caller summary as human-readable text.

        Args:
            summary: Dict from get_caller_summary()

        Returns:
            Formatted multi-line string
        """
        if not summary:
            return "No records found."
        lines = []
        for caller, stats in summary.items():
            lines.append(f"Caller: {caller}")
            lines.append(f" Calls: {stats['call_count']}")
            lines.append(f" Input tokens: {stats['input_tokens']}")
            lines.append(f" Output tokens: {stats['output_tokens']}")
            lines.append(f" Cache read: {stats['cache_read_input_tokens']}")
            lines.append(f" Cache create: {stats['cache_creation_input_tokens']}")
            lines.append(f" Total tokens: {stats['total_tokens']}")
            lines.append(f" Total duration: {stats['total_duration_ms']}ms")
        return "\n".join(lines)

    def format_model_summary(self, summary: Dict[str, Dict[str, int]]) -> str:
        """Format model summary as human-readable text.

        Args:
            summary: Dict from get_model_summary()

        Returns:
            Formatted multi-line string
        """
        if not summary:
            return "No records found."
        lines = []
        for model, stats in summary.items():
            lines.append(f"Model: {model}")
            lines.append(f" Calls: {stats['call_count']}")
            lines.append(f" Input tokens: {stats['input_tokens']}")
            lines.append(f" Output tokens: {stats['output_tokens']}")
            lines.append(f" Cache read: {stats['cache_read_input_tokens']}")
            lines.append(f" Cache create: {stats['cache_creation_input_tokens']}")
            lines.append(f" Total tokens: {stats['total_tokens']}")
            lines.append(f" Total duration: {stats['total_duration_ms']}ms")
        return "\n".join(lines)

    def format_total_summary(self, summary: Dict[str, int]) -> str:
        """Format total summary as human-readable text.

        Args:
            summary: Dict from get_total_summary()

        Returns:
            Formatted multi-line string
        """
        lines = [
            "=== Token Usage Statistics ===",
            f"Total calls: {summary['call_count']}",
            f"Input tokens: {summary['input_tokens']}",
            f"Output tokens: {summary['output_tokens']}",
            f"Cache read input tokens: {summary['cache_read_input_tokens']}",
            f"Cache creation input tokens: {summary['cache_creation_input_tokens']}",
            f"Total tokens: {summary['total_tokens']}",
            f"Total duration: {summary['total_duration_ms']}ms",
        ]
        return "\n".join(lines)

    def format_all_by_caller(self) -> str:
        """Format all records grouped by caller."""
        summary = self.get_caller_summary()
        if not summary:
            return "No records found."
        lines = ["=== Token Records by Caller ==="]
        for caller in sorted(summary.keys()):
            lines.append(f"\n--- {caller} ---")
            records = self.get_records_by_caller(caller)
            lines.append(self.format_records(records))
        return "\n".join(lines)

    def format_all_by_model(self) -> str:
        """Format all records grouped by model."""
        summary = self.get_model_summary()
        if not summary:
            return "No records found."
        lines = ["=== Token Records by Model ==="]
        for model in sorted(summary.keys()):
            lines.append(f"\n--- {model} ---")
            records = self.get_records_by_model(model)
            lines.append(self.format_records(records))
        return "\n".join(lines)

    def output(self, args: Optional[List[str]] = None) -> str:
        """Generate output based on command-line arguments.

        Args:
            args: List of command-line arguments (e.g., ['--by-caller', '--caller', 'lead'])

        Returns:
            Formatted output string
        """
        parser = argparse.ArgumentParser(prog="/tokens", add_help=False)
        parser.add_argument("--by-caller", action="store_true", help="Show records grouped by caller")
        parser.add_argument("--caller", type=str, help="Filter by specific caller")
        parser.add_argument("--summary", action="store_true", help="Show summary by caller")
        parser.add_argument("--total", action="store_true", help="Show total summary")
        parser.add_argument("--by-model", action="store_true", help="Show records grouped by model")
        parser.add_argument("--model", type=str, help="Filter by specific model")
        parser.add_argument("--model-summary", action="store_true", help="Show summary by model")

        try:
            parsed, _ = parser.parse_known_args(args if args is not None else [])
        except SystemExit:
            return self.format_total_summary(self.get_total_summary())

        if parsed.by_caller:
            return self.format_all_by_caller()

        if parsed.caller:
            records = self.get_records_by_caller(parsed.caller)
            return self.format_records(records)

        if parsed.summary:
            return self.format_caller_summary(self.get_caller_summary())

        if parsed.by_model:
            return self.format_all_by_model()

        if parsed.model:
            records = self.get_records_by_model(parsed.model)
            return self.format_records(records)

        if parsed.model_summary:
            return self.format_model_summary(self.get_model_summary())

        return self.format_total_summary(self.get_total_summary())

    def to_list(self) -> List[Dict]:
        """Serialize all records to a list of dicts for persistence."""
        result = []
        for r in self._records:
            result.append({
                "caller": r.caller,
                "timestamp": r.timestamp.isoformat(),
                "duration_ms": r.duration_ms,
                "model": r.model,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cache_creation_input_tokens": r.cache_creation_input_tokens,
                "cache_read_input_tokens": r.cache_read_input_tokens,
            })
        return result

    def load_from_list(self, data: List[Dict]) -> None:
        """Restore records from a list of dicts (from persistence).

        Appends to existing records (does not clear first).
        """
        for item in data:
            record = TokenRecord(
                caller=item["caller"],
                timestamp=datetime.fromisoformat(item["timestamp"]),
                duration_ms=item["duration_ms"],
                model=item["model"],
                input_tokens=item.get("input_tokens", 0),
                output_tokens=item.get("output_tokens", 0),
                cache_creation_input_tokens=item.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=item.get("cache_read_input_tokens", 0),
            )
            self._records.append(record)

    def reset(self) -> None:
        """Reset all records (for testing)."""
        self._records.clear()

    @staticmethod
    def get_tracker() -> "TokenTracker":
        """Get the singleton TokenTracker instance."""
        return TokenTracker()

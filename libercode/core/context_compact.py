"""Context compression for LiberCode agents.

Two-stage compaction:

    Stage 1: tool_result_compact — persist large tool results to disk (before LLM call)
    Stage 2: full_compact         — LLM summary triggered by token ratio check (after LLM call)

    Emergency: reactive_compact — when API returns prompt_too_long
"""

import json
import threading
from pathlib import Path
from typing import List, Dict, Optional

MAX_REACTIVE_RETRIES: int = 2


class CompactionRecord:
    def __init__(self) -> None:
        self.tool_result_count: int = 0
        self.history_count: int = 0
        self.reactive_count: int = 0
        self.last_type: str = ""

    @property
    def total(self) -> int:
        return self.tool_result_count + self.history_count + self.reactive_count

    def to_dict(self) -> dict:
        return {
            "tool_result_count": self.tool_result_count,
            "history_count": self.history_count,
            "reactive_count": self.reactive_count,
            "last_type": self.last_type,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CompactionRecord":
        r = cls()
        r.tool_result_count = d.get("tool_result_count", 0)
        r.history_count = d.get("history_count", 0)
        r.reactive_count = d.get("reactive_count", 0)
        r.last_type = d.get("last_type", "")
        return r


class CompactionTracker:
    _records: Dict[str, CompactionRecord] = {}
    _stats_path: Optional[Path] = None
    _dirty: bool = False
    _lock: threading.RLock = threading.RLock()

    @classmethod
    def init(cls, compacts_dir: Path) -> None:
        with cls._lock:
            cls._stats_path = compacts_dir / "stats.json"
            cls._stats_path.parent.mkdir(parents=True, exist_ok=True)
            cls._records.clear()
            if cls._stats_path.exists():
                try:
                    data = json.loads(cls._stats_path.read_text())
                    for name, rec in data.get("records", {}).items():
                        cls._records[name] = CompactionRecord.from_dict(rec)
                except Exception:
                    cls._records.clear()

    @classmethod
    def get(cls, agent_name: str) -> CompactionRecord:
        with cls._lock:
            if agent_name not in cls._records:
                cls._records[agent_name] = CompactionRecord()
            return cls._records[agent_name]

    @classmethod
    def record_compact(cls, agent_name: str, compact_type: str) -> None:
        with cls._lock:
            rec = cls.get(agent_name)
            if compact_type == "tool_result":
                rec.tool_result_count += 1
            elif compact_type == "history":
                rec.history_count += 1
            elif compact_type == "reactive":
                rec.reactive_count += 1
            rec.last_type = compact_type
            cls._dirty = True
        cls._flush()

    @classmethod
    def has_compacted(cls, agent_name: str) -> bool:
        with cls._lock:
            return cls.get(agent_name).total > 0

    @classmethod
    def _flush(cls) -> None:
        with cls._lock:
            if not cls._dirty or not cls._stats_path:
                return
            data = {
                "records": {name: rec.to_dict() for name, rec in cls._records.items()},
            }
            cls._stats_path.write_text(json.dumps(data, indent=2))
            cls._dirty = False

    @classmethod
    def load_from(cls, path: Path) -> None:
        with cls._lock:
            cls._records.clear()
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    for name, rec in data.get("records", {}).items():
                        cls._records[name] = CompactionRecord.from_dict(rec)
                except Exception:
                    pass

    @classmethod
    def get_summary(cls, agent_name: str) -> str:
        rec = cls.get(agent_name)
        parts = []
        if rec.tool_result_count:
            parts.append(f"T{rec.tool_result_count}")
        if rec.history_count:
            parts.append(f"H{rec.history_count}")
        if rec.reactive_count:
            parts.append(f"R{rec.reactive_count}")
        return ",".join(parts) if parts else ""


def _is_tool_result_message(msg: Dict) -> bool:
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def _message_has_tool_use(msg: Dict) -> bool:
    if msg.get("role") != "assistant":
        return False
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if block_type == "tool_use":
            return True
    return False


def _persist_large_output(tool_use_id: str, output: str, compacts_dir: Path, persist_threshold: int) -> str:
    if len(output) <= persist_threshold:
        return output
    results_dir = compacts_dir / "tool-results"
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{tool_use_id}.txt"
    if not path.exists():
        path.write_text(output)
    return (
        f"<persisted-output>\n"
        f"Full output: {path}\n"
        f"Preview:\n{output[:2000]}\n"
        f"</persisted-output>"
    )


def tool_result_compact(
    messages: List[Dict],
    compacts_dir: Path,
    persist_threshold: int = 30000,
    fullsize_threshold: int = 200_000,
) -> None:
    last = messages[-1] if messages else None
    if not last or last.get("role") != "user" or not isinstance(last.get("content"), list):
        return
    blocks = [
        (i, b) for i, b in enumerate(last["content"])
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    total = sum(len(str(b.get("content", ""))) for _, b in blocks)
    if total <= fullsize_threshold:
        return
    ranked = sorted(blocks, key=lambda p: len(str(p[1].get("content", ""))), reverse=True)
    for _, block in ranked:
        if total <= fullsize_threshold:
            break
        content = str(block.get("content", ""))
        if len(content) <= persist_threshold:
            continue
        tid = block.get("tool_use_id", "unknown")
        block["content"] = _persist_large_output(tid, content, compacts_dir, persist_threshold)
        total = sum(len(str(b.get("content", ""))) for _, b in blocks)


def _summarize_history(messages: List[Dict], client, model_id: str) -> str:
    conversation = json.dumps(messages, default=str)
    prompt = (
        "Summarize this coding-agent conversation so work can continue.\n"
        "Preserve: 1. current goal, 2. key findings/decisions, 3. files read/changed, "
        "4. remaining work, 5. user constraints.\nBe compact but concrete.\n\n"
        + conversation
    )
    response = client.messages.create(
        model=model_id,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=6000,
    )
    return (
        "\n".join(
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", None) == "text"
        ).strip()
        or "(empty summary)"
    )


async def _async_summarize_history(messages: List[Dict], async_client, model_id: str) -> str:
    conversation = json.dumps(messages, default=str)
    prompt = (
        "Summarize this coding-agent conversation so work can continue.\n"
        "Preserve: 1. current goal, 2. key findings/decisions, 3. files read/changed, "
        "4. remaining work, 5. user constraints.\nBe compact but concrete.\n\n"
        + conversation
    )
    response = await async_client.messages.create(
        model=model_id,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=6000,
    )
    return (
        "\n".join(
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", None) == "text"
        ).strip()
        or "(empty summary)"
    )


def needs_compact(input_tokens: int, context_window: int, ratio_threshold: float) -> bool:
    if context_window <= 0:
        return False
    return (input_tokens / context_window) > ratio_threshold


def compact_history(messages: List[Dict], client, model_id: str, keep_latest: int = 10, keep_original: int = 10) -> None:
    if len(messages) <= keep_latest + keep_original:
        return
    head = messages[:keep_original]
    tail = messages[-keep_latest:]
    to_compact = messages[keep_original:-keep_latest]
    summary = _summarize_history(to_compact, client, model_id)
    messages.clear()
    messages.extend(head)
    messages.append({"role": "user", "content": f"[Compacted]\n\n{summary}"})
    messages.extend(tail)


async def async_compact_history(messages: List[Dict], async_client, model_id: str, keep_latest: int = 10, keep_original: int = 10) -> None:
    if len(messages) <= keep_latest + keep_original:
        return
    head = messages[:keep_original]
    tail = messages[-keep_latest:]
    to_compact = messages[keep_original:-keep_latest]
    summary = await _async_summarize_history(to_compact, async_client, model_id)
    messages.clear()
    messages.extend(head)
    messages.append({"role": "user", "content": f"[Compacted]\n\n{summary}"})
    messages.extend(tail)


def reactive_compact(messages: List[Dict], client, model_id: str) -> None:
    summary = _summarize_history(messages, client, model_id)
    tail_start = max(0, len(messages) - 5)
    if (tail_start > 0
            and tail_start < len(messages)
            and _is_tool_result_message(messages[tail_start])
            and _message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    tail = list(messages[tail_start:])
    messages.clear()
    messages.append({"role": "user", "content": f"[Reactive compact]\n\n{summary}"})
    messages.extend(tail)


async def async_reactive_compact(messages: List[Dict], async_client, model_id: str) -> None:
    summary = await _async_summarize_history(messages, async_client, model_id)
    tail_start = max(0, len(messages) - 5)
    if (tail_start > 0
            and tail_start < len(messages)
            and _is_tool_result_message(messages[tail_start])
            and _message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    tail = list(messages[tail_start:])
    messages.clear()
    messages.append({"role": "user", "content": f"[Reactive compact]\n\n{summary}"})
    messages.extend(tail)

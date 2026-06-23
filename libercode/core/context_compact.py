"""Context compression for LiberCode agents.

Two-stage compaction:

    Stage 1: tool_result_compact — persist large tool results to disk (before LLM call)
    Stage 2: full_compact         — LLM summary triggered by token ratio check (after LLM call)

    Emergency: reactive_compact — when API returns prompt_too_long
"""

import json
from pathlib import Path
from typing import List, Dict

MAX_REACTIVE_RETRIES: int = 2


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


def _persist_large_output(tool_use_id: str, output: str, workdir: Path, persist_threshold: int) -> str:
    if len(output) <= persist_threshold:
        return output
    results_dir = workdir / ".compacts" / "tool-results"
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
    workdir: Path,
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
        block["content"] = _persist_large_output(tid, content, workdir, persist_threshold)
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

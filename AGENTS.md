# LiberCode

Multi-agent AI coding assistant. Lead agent orchestrates autonomous teammate agents via JSONL message passing, using Anthropic's Claude API.

## Setup

```bash
export LLM_API_KEY=<key>
export MODEL_ID=<model-id>
export LLM_BASE_URL=<url>  # optional for Anthropic; required for other providers
pip install -r requirements.txt
pip install .
```

No tests, linter, or typechecker configured.

## Running

```bash
tmux new-session -s LiberCode "libercode; echo 'press enter to exit...'; read"  # full UI (status pane)
libercode                                                          # no tmux (no status pane)
python -m libercode                                                # alternative entry point
```

Entry point: `libercode/cli.py:main` (registered in `pyproject.toml` as `libercode` console script).

## Environment Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `LLM_API_KEY` | (required) | Anthropic API key |
| `MODEL_ID` | (required) | Model identifier |
| `LLM_BASE_URL` | none | Required for non-Anthropic providers |
| `LIBERCODE_DEBUG` | `false` | Set `true` for debug logging |
| `LIBERCODE_STATUS_REFRESH` | `5.0` | Set `0.0` to disable status pane (macOS workaround) |
| `LIBERCODE_SESSION_AUTO_SAVE` | `true` | Auto-save sessions |
| `LIBERCODE_SESSION_INTERVAL` | `1.0` | Auto-save interval (seconds) |

## Architecture

Key wiring: `cli.py:main` creates `Config`, `TaskManager`, `MessageBus`, `TeammateManager`, `LeadAgent`, then runs `async_repl_loop`. Teammates are spawned by the lead agent; each runs in its own daemon thread with its own `TeammateAgent` instance.

```
libercode/
  core/lead.py              — Lead agent (orchestrator)
  core/teammate.py          — Teammate agent (autonomous worker)
  core/teammate_manager.py  — Teammate lifecycle management
  core/interrupt_handler.py — Ctrl+C cancellation support
  messaging/bus.py          — JSONL message bus (append-only inbox files)
  messaging/protocol.py     — MessageType enum + Message dataclass
  messaging/serialization.py — Anthropic SDK object → JSON-serializable
  taskboard/manager.py      — Task CRUD (JSON files in .tasks/)
  taskboard/models.py       — Task dataclass (dependencies use blockedBy in JSON)
  tools/lead_tools.py       — 17 lead tools
  tools/teammate_tools.py   — 13 teammate tools
  tools/base.py             — Shared file/bash tool implementations
  tools/validator.py        — Tool input schema validation and coercion
  tools/worktree_tools.py   — 8 worktree tools (DEFINED BUT NOT WIRED)
  worktree/                 — Git worktree isolation (DEFINED BUT NOT WIRED)
  ui/                       — Tmux panes, status display, input, output
  config.py                 — Env loading, paths, AsyncAnthropic client init
  cli.py                    — REPL loop, session management, agent orchestration
  session_manager.py        — Session save/restore/auto-save
  exceptions.py             — Custom exception hierarchy
  utils/logging.py          — Structured logging (rotating files)
  utils/token_tracker.py    — Token usage tracking (singleton)
  prompts/                  — System prompts: lead_system.txt, teammate_system.txt, init_agents_md.txt, review.txt
```

## Runtime Files (created in cwd)

- `.team/inbox/<name>.jsonl` — agent message inboxes
- `.team/config.json` — team member configuration
- `.tasks/task_<id>.json` — task JSON files (uses `blockedBy` key, not `blocked_by`)
- `.libercode/sessions/<name>/` — session save data
- `.libercode/sessions/<name>/libercode.log` — rotating debug log

These runtime directories (`.team/`, `.tasks/`) are cleaned up on exit unless session auto-save is enabled.

## REPL Commands

| Command | Description |
|---------|-------------|
| `/init` | Regenerate AGENTS.md (uses `libercode/prompts/init_agents_md.txt`) |
| `/review [args]` | Code review (uses `libercode/prompts/review.txt`) |
| `/team` | List teammates |
| `/tasks` | Show task board |
| `/inbox` | Check lead's messages |
| `/tokens` | Token usage stats |
| `/sessions [list\|restore\|delete\|resubject]` | Session management |
| `/clear [name\|all]` | Clear message history |
| `q` / `exit` | Quit |
| `!<cmd>` | Run shell command directly from REPL |

## Conventions

- Python >=3.10, type hints throughout, docstrings on public APIs
- Custom exception hierarchy in `exceptions.py` — always use these, never `raise Exception`
- Both lead and teammate agents read `AGENTS.md` or `CLAUDE.md` from project root as `<project_instructions>` — injected once, never re-injected
- Task JSON files use legacy key `blockedBy` in serialization (`Task.from_dict` accepts both `blockedBy` and `blocked_by`, but `to_dict` always writes `blockedBy`)
- Prompts are shipped as package data (`libercode/prompts/*.txt`) via `pyproject.toml` `[tool.setuptools.package-data]`
- `Config.__init__` calls `load_dotenv(override=False)` — existing env vars take priority over `.env`
- Console logging is set to ERROR level by default; file logging is DEBUG

## Extending

- New tools: add definition + handler to `tools/lead_tools.py` or `tools/teammate_tools.py` (also add handler registration in `create_lead_tool_handlers()` / `create_teammate_tool_handlers()`)
- New message types: add to `MessageType` enum in `messaging/protocol.py`
- New task fields: add to `Task` dataclass in `taskboard/models.py` (update `to_dict`/`from_dict` for serialization)
- Worktree tools at `tools/worktree_tools.py` and `worktree/` are defined but not wired into the runtime

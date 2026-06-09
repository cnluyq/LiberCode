# LiberCode

Multi-agent AI coding assistant. Lead agent orchestrates autonomous teammate agents via JSONL message passing, using Anthropic's Claude API.

## Setup

```bash
export LLM_API_KEY=<key>
export MODEL_ID=<model-id>
export LLM_BASE_URL=<url> # optional for Anthropic; required for other providers
pip install -r requirements.txt
pip install .
```

No tests, linter, or typechecker configured.

## Running

```bash
tmux new-session -s LiberCode "libercode; echo 'press enter to exit...'; read" # full UI (status pane)
libercode # no tmux (no status pane)
python -m libercode # alternative entry point
```

Entry point: `libercode/cli.py:main` (registered in `pyproject.toml` as `libercode` console script).

## Configuration

LiberCode reads project-level settings from `libercode.json` in the working directory. API credentials (`LLM_API_KEY`, `MODEL_ID`, `LLM_BASE_URL`) are always read from environment variables / `.env` and should **not** be placed in the config file.

### libercode.json

```jsonc
{
  // Runtime
  "debug": false, // Enable debug logging (default: false)
  "status_refresh": 5.0, // Status pane refresh interval in seconds; 0 disables pane (default: 5.0)

  // Session auto-save
  "session_auto_save": true, // Enable auto-save (default: true)
  "session_auto_save_interval": 1.0, // Auto-save interval in seconds (default: 1.0)

  // Dangerous command control
  "dangerous_command_policy": "deny", // "deny" (block) | "allow" (pass) | "confirm" (ask user)
  "dangerous_command_patterns_override": null, // Array to replace defaults entirely; [] disables all checking
  "dangerous_command_patterns_extra": [] // Array of additional patterns to append to defaults
}
```

If `libercode.json` does not exist, built-in defaults are used for every setting.

### Model Configuration (3-tier priority)

Model `context_window` and `output_max` are resolved per-field in this order:

1. **`libercode.json`** `models` entry — user-level overrides (highest priority)
2. **`models.json`** — system-level defaults shipped with the package (`libercode/models.json`)
3. **Hardcoded defaults** — `default_context_window` (256,000) and `default_output_max` (8,192)

**Example** — override a single model's context window in `libercode.json`:

```jsonc
{
  "models": {
    "my-custom-model": { "context_window": 128000, "output_max": 4096 }
  }
}
```

For `my-custom-model`: uses 128000 / 4096 from `libercode.json`. For any model not in `libercode.json` or `models.json`: uses 256000 (hardcoded `default_context_window`) and 8192 (hardcoded `default_output_max`).

### Dangerous Command Patterns

Each pattern is a string in `[type:]pattern` format:

| Type | Example | Behavior |
|------|---------|----------|
| `prefix` (default) | `sudo` or `prefix:sudo` | Substring match (`"sudo" in command`) |
| `glob` | `glob:rm -rf *` | fnmatch shell-style wildcard on the full command |
| `regex` | `regex:^dd\\s+if=` | Regular expression search |

**Default patterns** (active when no override is set):

```json
[
  "prefix:rm -rf /",
  "prefix:sudo",
  "prefix:shutdown",
  "prefix:reboot"
]
```

**Override example** — replace defaults with your own list:

```json
{
  "dangerous_command_patterns_override": [
    "prefix:sudo",
    "glob:rm *",
    "regex:^dd\\s"
  ]
}
```

**Empty override** — disable all dangerous command checking:

```json
{
  "dangerous_command_patterns_override": []
}
```

**Extra example** — append patterns to the defaults:

```json
{
  "dangerous_command_patterns_extra": [
    "glob:curl *",
    "regex:^python3.*http"
  ]
}
```

### Environment Variables

| Variable | Notes |
|----------|-------|
| `LLM_API_KEY` | (required) Anthropic API key |
| `MODEL_ID` | (required) Model identifier |
| `LLM_BASE_URL` | Required for non-Anthropic providers |

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
  tools/base.py             — Shared file/bash tool implementations + dangerous command policy
  tools/validator.py        — Tool input schema validation and coercion
  tools/worktree_tools.py   — 8 worktree tools (DEFINED BUT NOT WIRED)
  worktree/                 — Git worktree isolation (DEFINED BUT NOT WIRED)
  ui/                       — Tmux panes, status display, input, output
  config.py                 — libercode.json loading, env loading, AsyncAnthropic client init
  models.json               — System-level model defaults (context_window / output_max per model)
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
- System model defaults are shipped as package data (`libercode/models.json`) via `pyproject.toml` — loaded by `_load_system_models()` in `config.py`
- `Config.__init__` loads `libercode.json` from cwd then calls `load_dotenv(override=False)` — existing env vars take priority over `.env`
- API credentials (`LLM_API_KEY`, `MODEL_ID`, `LLM_BASE_URL`) must be in env vars / `.env`, never in `libercode.json`
- Console logging is set to ERROR level by default; file logging is DEBUG

## Extending

- New tools: add definition + handler to `tools/lead_tools.py` or `tools/teammate_tools.py` (also add handler registration in `create_lead_tool_handlers()` / `create_teammate_tool_handlers()`)
- New message types: add to `MessageType` enum in `messaging/protocol.py`
- New task fields: add to `Task` dataclass in `taskboard/models.py` (update `to_dict`/`from_dict` for serialization)
- Worktree tools at `tools/worktree_tools.py` and `worktree/` are defined but not wired into the runtime

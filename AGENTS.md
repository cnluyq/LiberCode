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
libercode # unified command: auto-detects tmux (see below)
libercode -c # continue the most recent saved session (also: --continue)
python -m libercode # alternative entry point
```

Startup auto-detection (`cli.py:_maybe_relaunch_in_tmux`): if the `tmux` binary exists, stdin/stdout are terminals, and we are not already inside tmux, LiberCode re-executes itself inside a `tmux new-session -s LiberCode` session (full UI with status pane). If a `LiberCode` session already exists it attaches to it instead. Otherwise (no tmux, non-tty, or tmux failure) it runs directly without a status pane.

With `-c` / `--continue`, LiberCode restores the most recent saved session at startup (`cli.py:_restore_latest_session`, via `SessionRecoveryManager.restore_session`): lead messages, tasks, teammates, inboxes, and token records. If no saved sessions exist it falls back to a fresh session. Note: `SessionRecoveryManager.list_sessions` skips session directories without a `meta.json` (e.g. log-only dirs created by `setup_logging`).

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
  "dangerous_command_patterns_extra": [], // Array of additional patterns to append to defaults
  "dangerous_command_timeout": 1800, // Teammate confirm wait timeout in seconds (default: 1800 = 30min)

  // Model routing (see "Model Routing" section below)
  "model_router": {} // {} disables routing (default); see below for the routing schema
}
```

If `libercode.json` does not exist, built-in defaults are used for every setting.

A complete template with every option (including `model_router`) is shipped at `libercode.json.example` — copy it to `libercode.json` and edit. Plain JSON only: comments are not allowed.

### Model Routing (multi-model auto-selection)

`MODEL_ID` (or `/model`) sets the **global model**. When `model_router.enabled` is true, LiberCode can additionally assign different models to the lead and to individual teammates/tasks at runtime, selected by task characteristics. All routed models must be available on the currently connected provider (same `LLM_API_KEY` / `LLM_BASE_URL` — only the `model` argument of the API call differs).

Resolution order for a teammate/task (implemented in `libercode/model_router.py` `ModelRouter.resolve`):

1. Explicit `model` (lead's `spawn_teammate` / `task_create` tool argument)
2. Runtime role override (`/route role`)
3. `by_keyword` — first keyword match on the task subject + description / spawn prompt
4. `by_role` — match on the teammate role
5. Runtime default override (`/route default`)
6. `default`
7. Global model (`MODEL_ID` / `/model`)

When no rule matches, the model is **not pinned**: the agent keeps following global `/model` switches. When a rule matches (or the optional classifier hits), the model is pinned at spawn/task-creation time. The lead uses the `lead` rule (default: global model). A teammate that claims a task with a pinned `model` field switches to it. Teammate models are persisted in `.team/config.json` and restored with sessions.

**Validation against the provider model list**: every candidate model — LLM-provided explicit values, rule results, classifier results, and task `model` fields at claim time — is validated before use (`Config.validate_model` / `Config.verify_model_sync`). The authoritative model id set is cached from the provider model-list endpoint at startup (`refresh_pricing`), after `/connect`, and with `/model list` (`Config.update_fetched_model_info` → `_available_model_ids`). Validation order: (1) models declared in `libercode.json` `model_params` (escape hatch for aliases not exposed by the list endpoint), (2) probe cache, (3) provider list (deterministic, zero-cost; unknown → fall back to a 1-token probe), (4) provider-reported limits/pricing. Invalid candidates are dropped with a warning and fall back to the next rule (ultimately the global model); `/model routes` flags configured-but-unavailable models as `[UNAVAILABLE]`, and `resolve_lead_model` deterministically falls back to the global model when the configured `lead` model is not on the provider list.

**Example**:

```jsonc
{
  "model_router": {
    "enabled": true,
    "lead": "claude-opus-4",            // lead always uses this model
    "default": "claude-sonnet-4",       // fallback for routed teammates
    "by_role": {                        // role → model
      "frontend": "claude-sonnet-4",
      "backend": "claude-opus-4"
    },
    "by_keyword": [                     // task-text → model (first match wins)
      { "keywords": ["test", "unit test", "refactor"], "model": "deepseek-flash" }
    ],
    "classifier": {                     // optional LLM classifier for tasks matching no rule
      "model": "deepseek-flash",
      "categories": [
        { "name": "trivial", "model": "deepseek-flash", "description": "simple mechanical edits, typos, formatting, small config changes" },
        { "name": "complex", "model": "claude-opus-4", "description": "complex architecture, debugging, security-sensitive or large-scale work" }
      ]
    }
  }
}
```

Inspect routing with the `/model routes` REPL command; `/model` shows routing status alongside the global model, lists teammates pinned by the router, and prints a note after switching when routed agents are unaffected by the new global model. Context compaction uses each agent's active model (not the global one).

**Runtime overrides (`/route` command)**: routing rules can be changed at runtime without editing `libercode.json`:

```
/route                                  # show current overrides + pinned agents
/route agent <name> <model|reset>       # change one teammate's model (persisted via team config)
/route role <role> <model>              # override a by_role mapping
/route default <model>                  # override the default rule
/route lead <model>                     # override the lead rule
/route reset                            # clear all rule overrides
```

Rule overrides (role/default/lead) are evaluated before the configured `by_keyword`/`by_role`/`default` rules and are persisted with sessions (`routes.json`, restored by `SessionRecoveryManager.restore_session`). They only take effect when routing is enabled — `/route` warns when it is not. `agent` changes update the live teammate immediately (next LLM call) and survive session restore via the team config member record. All override models are validated against the provider model list.

### Model Configuration (3-tier priority)

`model_params` is a **parameter library for known models**, not a list of available models: entries never cause a model to be used — they only override `context_window` / `output_max` / `pricing` once a model is selected (via `MODEL_ID`, `/model`, or `model_router`). A model listed here also counts as user-declared-available during provider-list validation. The legacy key `models` still loads (with a deprecation warning) but is superseded by `model_params`.

Model `context_window` and `output_max` are resolved per-field in this order:
1. **`libercode.json`** `model_params` entry — user-level overrides (highest priority)
2. **`models.json`** — system-level defaults shipped with the package (`libercode/models.json`)
3. **Auto-fetched** — provider model list (`context_window` / `max_output_tokens`) then models.dev catalog (`limit.context` / `limit.output`); refreshed at startup, after `/connect`, and with `/model list`
4. **Hardcoded defaults** — `default_context_window` (256,000) and `default_output_max` (8,192)

The status pane shows `N/A (context window unknown)` when a model's limits are unknown (no config entry and not exposed by provider/models.dev). `Config.get_model_config_source(model_id)` reports the source (`libercode.json` / `models.json` / `provider` / `models.dev` / `None`).

**Example** — override a single model's context window in `libercode.json`:

```jsonc
{
  "model_params": {
    "my-custom-model": { "context_window": 128000, "output_max": 4096 }
  }
}
```

For `my-custom-model`: uses 128000 / 4096 from `libercode.json`. For any model not in `libercode.json` or `models.json`: uses 256000 (hardcoded `default_context_window`) and 8192 (hardcoded `default_output_max`).

### Model Pricing (cost statistics)

Token cost is computed locally from a per-model `pricing` dict (USD per 1M tokens); the LLM API response contains only token counts, never cost data. Resolution order:

1. **`libercode.json`** `model_params` entry `pricing` — user-level overrides (highest priority)
2. **`models.json`** — system-level defaults shipped with the package

**Example** — configure pricing for a model in `libercode.json`:

```jsonc
{
  "model_params": {
    "my-custom-model": {
      "pricing": { "input": 0.27, "output": 1.10, "cache_read": 0.07, "cache_creation": 0.34 }
    }
  }
}
```

`input` and `output` are required; `cache_read` / `cache_creation` are optional and default to Anthropic's convention (0.1x / 1.25x the input price). Models without pricing show `N/A` (or "unknown pricing") in cost displays. Cost appears in the status pane Token Summary, `/tokens` outputs, and is persisted in session `token_records.json`.

In addition, LiberCode automatically fetches pricing from two sources (`Config.refresh_pricing()`, run at startup, after `/connect`, and with `/model list`):

1. **Connected provider's model list endpoint** — OpenRouter (`pricing.prompt` / `pricing.completion`, per-token), LiteLLM (`input_cost_per_token` / `output_cost_per_token`), and per-1M (`pricing.input` / `pricing.output`)
2. **models.dev community catalog** (`https://models.dev/api.json`) — cached to `~/.cache/libercode/models-dev.json` for 24h, stale cache used as fallback

Providers that expose no pricing and are absent from models.dev yield `N/A`. Resolution order: `libercode.json` → `models.json` → provider auto-fetch → models.dev.

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
  config.py                 — libercode.json loading, env loading, sync+async client init, runtime connect()/set_model()/persist_env()
  model_router.py           — Multi-model routing rules (by_role/by_keyword/default + optional LLM classifier)
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
| `/route` | Show/change runtime routing overrides: `agent <name> <model\|reset>`, `role <role> <model>`, `default <model>`, `lead <model>`, `reset` (persisted with sessions) |
| `/tokens` | Token usage stats |
| `/sessions [list\|restore\|delete\|resubject]` | Session management |
| `/clear [name\|all]` | Clear message history |
| `/connect [api_key] [base_url]` | Switch LLM provider at runtime; interactive if no args. Validated with a 1-token probe call (reverted on failure); updates lead + all teammates and persists to `.env` |
| `/model [model_id\|list\|routes]` | Show current model (with routing status), switch model (validated against the provider model list / a 1-token probe, deterministic when the list is cached), list available models (`/model list`), or show routing rules (`/model routes`); persists to `.env` |
| `q` / `exit` | Quit |
| `!<cmd>` | Run shell command directly from REPL |

## Conventions

- Python >=3.10, type hints throughout, docstrings on public APIs
- Custom exception hierarchy in `exceptions.py` — always use these, never `raise Exception`
- Both lead and teammate agents read `AGENTS.md` or `CLAUDE.md` from project root as `<project_instructions>` — injected once, never re-injected
- `libercode/ui/input_handler.py` sets `PROMPT_TOOLKIT_NO_CPR=1` at import time: the REPL is a single-line prompt with no bottom floating menus, so prompt_toolkit's cursor position requests (CPR, `\x1b[6n`) are disabled — otherwise a slow/missing CPR response would print "your terminal doesn't support cursor position requests (CPR)" into the prompt and leave stale escape sequences in the input stream
- Task JSON files use legacy key `blockedBy` in serialization (`Task.from_dict` accepts both `blockedBy` and `blocked_by`, but `to_dict` always writes `blockedBy`)
- Prompts are shipped as package data (`libercode/prompts/*.txt`) via `pyproject.toml` `[tool.setuptools.package-data]`
- System model defaults are shipped as package data (`libercode/models.json`) via `pyproject.toml` — loaded by `_load_system_models()` in `config.py`
- `Config.__init__` loads `libercode.json` from cwd then calls `load_dotenv(override=False)` — existing env vars take priority over `.env`
- API credentials (`LLM_API_KEY`, `MODEL_ID`, `LLM_BASE_URL`) must be in env vars / `.env`, never in `libercode.json`; at runtime they can be switched via `/connect` / `/model`, which write back to `.env` through `Config.persist_env()`
- Console logging is set to ERROR level by default; file logging is DEBUG

## Extending

- New tools: add definition + handler to `tools/lead_tools.py` or `tools/teammate_tools.py` (also add handler registration in `create_lead_tool_handlers()` / `create_teammate_tool_handlers()`)
- New message types: add to `MessageType` enum in `messaging/protocol.py`
- New task fields: add to `Task` dataclass in `taskboard/models.py` (update `to_dict`/`from_dict` for serialization)
- Worktree tools at `tools/worktree_tools.py` and `worktree/` are defined but not wired into the runtime

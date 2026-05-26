# LiberCode

**An AI Agent for Real Tasks with Teams**

LiberCode is a multi-agent system that orchestrates autonomous AI teammates to collaboratively execute real-world tasks. Built on Anthropic's Claude API, it features a lead agent that manages task decomposition, teammate spawning, and workflow coordination.
<img width="1910" height="981" alt="libercode_screen" src="https://github.com/user-attachments/assets/3c1bb491-973d-4654-9ed5-8ebabf787830" />


## Features

### Multi-Agent Architecture
- **Lead Agent**: Orchestrates tasks, spawns teammates, monitors progress
- **Teammate Agents**: Autonomous workers that claim and execute tasks
- **Message Bus**: JSONL-based inter-agent communication system
- **Task Board**: Dependency-aware task management with blocking relationships

### Core Capabilities
- **Task Decomposition**: Automatically break down complex tasks into subtasks
- **Dependency Management**: Define task dependencies and blocking relationships
- **Autonomous Execution**: Teammates autonomously claim and work on tasks
- **Real-time Communication**: Message passing between agents with broadcasting support
- **Progress Monitoring**: Track task status and teammate activity
- **Tmux Integration**: Visual output in separate tmux panes for each teammate

### Developer-Friendly Design
- Clean modular architecture with separation of concerns
- Type-safe data models with comprehensive docstrings
- Custom exception hierarchy for robust error handling
- Token usage tracking across all LLM interactions
- Configurable via environment variables

## Architecture

```
libercode/
├── core/              # Core agent implementations
│   ├── lead.py        # Lead agent orchestrator
│   └── teammate.py    # Autonomous teammate agent
├── messaging/         # Inter-agent communication
│   ├── protocol.py    # Message types and serialization
│   ├── bus.py         # JSONL message bus
│   └── serialization.py
├── taskboard/         # Task management
│   ├── models.py      # Task data model with dependencies
│   └── manager.py     # Task CRUD operations
├── tools/             # Agent tool definitions
│   ├── base.py        # File operations and bash tools
│   ├── lead_tools.py  # Lead agent tools (14 tools)
│   └── teammate_tools.py  # Teammate tools
├── ui/                # User interface components
│   ├── tmux.py        # Tmux pane management
│   └── output.py      # Thread-safe output handling
├── utils/             # Utilities
│   └── token_tracker.py  # Token usage tracking
├── worktree/          # Git worktree management (planned)
├── config.py          # Configuration management
├── exceptions.py      # Custom exception hierarchy
└── cli.py             # REPL interface
```

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd LiberCode

#Create python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# Install LiberCode
pip install .

# Uninstall
pip uninstall libercode
```

## Usage

### Configuration

```bash
export LLM_API_KEY=<your-api-key>
export MODEL_ID=<model_id>
export LLM_BASE_URL=<llm_provider_url> # Base URL (optional for anthropic models; required for other anthropic-compatible providers)
#such as https://api.deepseek.com/anthropic, http://127.0.0.1:3456 for ccr, etc.

export LIBERCODE_DEBUG=true  #set debug mode
```

### Quick Start

```bash
# Run in tmux(recommended strongly for better output display)
tmux new-session -s LiberCode "libercode; echo 'press enter to exit...'; read"

# Run command if no tmux (there is no status display for this)
libercode

#example：
[user@localhost ~] tmux new-session -s LiberCode "libercode; echo 'press enter to exit...'; read"
LiberCode - AI Agent for Teams
Type 'q' or 'exit' to quit
Press Ctrl+C to interrupt LLM processing

[LiberCode] ❯❯ 任务：使用C语言创建简单计算器。如果需要，可以分解成多个子任务，并创建多个队员来完成。
```

### Log File
```bash
.libercode/logs/libercode.log
```

### REPL Commands

```
[LiberCode] ❯❯ Create a Python script to analyze CSV files
[LiberCode] ❯❯ /team # List all teammates
[LiberCode] ❯❯ /tasks # Show task board
[LiberCode] ❯❯ /inbox # Check lead's messages
[LiberCode] ❯❯ /tokens # Show token usage stats
[LiberCode] ❯❯ q # Quit
```

### Multi-line Input

LiberCode supports multi-line user input with the following features:

#### 1. Manual Multi-line Input

Type `\` at the end of a line and press Enter to continue on the next line:

```
[LiberCode] ❯❯ Create a program that \
            reads from a file \
            and processes the data
```

Press Enter directly (without `\`) to submit.

#### 2. Paste from Clipboard

Copy multi-line text from clipboard and paste with Ctrl+V. The content will be inserted without auto-submit. Press Enter to submit.

```
[LiberCode] ❯❯ hello how are you?
            what can you do for me?
```

#### 3. Cursor Navigation

- **Left/Right Arrow Keys**: Move cursor within the input text
- **Backspace**: Delete character before cursor
- **Enter**: Submit input (or continue if line ends with `\`)
[LiberCode] ❯❯ Create a Python script to analyze CSV files
[LiberCode] ❯❯ /team          # List all teammates
[LiberCode] ❯❯ /tasks         # Show task board
[LiberCode] ❯❯ /inbox         # Check lead's messages
[LiberCode] ❯❯ /tokens        # Show token usage stats
[LiberCode] ❯❯ q              # Quit
```

### Example Workflow

1. **User Input**: "Build a REST API for user authentication"
2. **Lead Agent**: 
   - Decomposes into tasks: "Design DB schema", "Implement endpoints", "Add tests"
   - Sets dependencies: tests block implementation
   - Spawns teammates: "db-architect", "api-developer", "test-engineer"
3. **Teammates**:
   - Autonomously claim available tasks
   - Execute work using tools (bash, file operations)
   - Send progress messages to lead
4. **Lead Agent**:
   - Monitors task completion
   - Updates task statuses
   - Coordinates across teammates

## Task Board System

Tasks are stored as JSON files in `.tasks/` directory:

```json
{
  "id": 1,
  "subject": "Implement authentication",
  "description": "Add JWT-based auth",
  "status": "pending",
  "blockedBy": [],
  "blocks": [2, 3],
  "owner": "",
  "worktree": ""
}
```

### Task Status
- `pending`: Available for claiming
- `in_progress`: Being worked on
- `completed`: Finished and unblocked dependent tasks

### Dependencies
- `blockedBy`: Task IDs that must complete first
- `blocks`: Task IDs that wait for this task
- Automatic unblocking when dependencies complete

## Message Protocol

Agents communicate via JSONL inbox files in `.team/inbox/`:

```json
{
  "type": "message",
  "from": "lead",
  "content": "Task #1 completed",
  "timestamp": 1234567890.123,
  "request_id": "abc123"  // Optional: for protocol messages
}
```

### Message Types
- `message`: Direct communication
- `broadcast`: Team-wide announcements
- `shutdown_request`: Graceful shutdown protocol
- `plan_approval_response`: Plan review workflow

## Tools Available

### Lead Agent Tools (14)
- File operations: `read_file`, `write_file`, `edit_file`
- Shell execution: `bash`
- Task management: `task_create`, `task_update`, `task_list`, `task_get`
- Team management: `spawn_teammate`, `list_teammates`
- Communication: `send_message`, `read_inbox`, `broadcast`
- Workflow: `shutdown_request`

### Teammate Agent Tools
- File operations: `read_file`, `write_file`, `edit_file`
- Shell execution: `bash`
- Task claiming: `claim_task`
- Communication: `send_message`, `read_inbox`
- Workflow: `idle`, `shutdown_response`, `plan_approval`

## Development

### Code Quality
- **Type Safety**: Full type hints with `typing` module
- **Documentation**: Comprehensive docstrings for all public APIs
- **Error Handling**: Custom exception hierarchy
- **Testing**: Modular design enables unit testing

### Extending LiberCode
1. **Add New Tools**: Define in `tools/lead_tools.py` or `tools/teammate_tools.py`
2. **Custom Agents**: Extend `TeammateAgent` class in `core/teammate.py`
3. **Message Types**: Add to `MessageType` enum in `messaging/protocol.py`
4. **Task Metadata**: Extend `Task` dataclass in `taskboard/models.py`

## Architecture Decisions

### Why JSONL for Messages?
- Simple append-only format for concurrent writes
- Easy to parse and debug
- Natural message queue behavior
- No database dependency

### Why File-Based Tasks?
- Human-readable and editable
- Git-trackable for version control
- No setup required (just `.tasks/` directory)
- Easy to inspect and debug

### Why Separate Lead/Teammate Roles?
- Clear responsibility separation
- Lead focuses on coordination and decomposition
- Teammates focus on execution
- Enables parallel autonomous work

## Contributing

Contributions welcome! Please ensure:
1. Type hints for all function signatures
2. Docstrings for public APIs
3. Exception handling with custom exceptions
4. Backward compatibility with existing APIs

## Notes

Coding assisted-by AI

/init and /review prompts are referred from opencode project

Test and run on Linux. May be some issue on MacOS for status pane output, disable it by 'export LIBERCODE_STATUS_REFRESH=0.0'

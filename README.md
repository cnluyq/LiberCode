# LiberCode

**An AI Agent for Real Tasks with Teams**

LiberCode is a multi-agent system that orchestrates autonomous AI teammates to collaboratively execute real-world tasks. Built on Anthropic's Claude API, it features a lead agent that manages task decomposition, teammate spawning, and workflow coordination.

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

# Install dependencies
pip install anthropic python-dotenv

# Set up environment
export ANTHROPIC_API_KEY="your-api-key"
export MODEL_ID="your-model"  # or your preferred model
export ANTHROPIC_BASE_URL=http://127.0.0.1:3456 #set if not default, such as by ccr

# Install LiberCode for general use
cd <absolute_path_of_LiberCode_repo> && pip install .
pip install <absolute_path_of_LiberCode_repo>

# Uninstall
pip uninstall libercode
```

## Usage

### Quick Start

```bash
# Run installed command
libercode

# Run LiberCode
python -m libercode

# Or using the CLI entry point
python libercode/cli.py
```

### REPL Commands

```
libercode >> Create a Python script to analyze CSV files
libercode >> /team          # List all teammates
libercode >> /tasks         # Show task board
libercode >> /inbox         # Check lead's messages
libercode >> /tokens        # Show token usage stats
libercode >> q              # Quit
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

## Configuration

Create a `.env` file in your working directory:

```env
ANTHROPIC_API_KEY=your-api-key
MODEL_ID=claude-sonnet-4-6
ANTHROPIC_BASE_URL=https://api.anthropic.com  # Optional: for custom endpoints
```

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

## Roadmap

- [ ] Git worktree integration for isolated task execution
- [ ] Web UI for monitoring and control
- [ ] Enhanced error recovery and retry logic
- [ ] Task prioritization and deadline management
- [ ] Teammate skill specialization
- [ ] Integration testing framework

## Comparison with Original Version

The refactored version maintains all functionality while providing:

| Aspect | Original | Refactored |
|--------|----------|------------|
| Lines of Code | ~970 (single file) | ~2800 (29 files) |
| Architecture | Monolithic script | Modular modules |
| Type Safety | Minimal | Full type hints |
| Documentation | Inline comments | Comprehensive docstrings |
| Error Handling | Basic | Custom exception hierarchy |
| Testability | Difficult | Modular, testable components |
| Maintainability | Hard to extend | Clear separation of concerns |
| Extensibility | Tightly coupled | Dependency injection |

## Contributing

Contributions welcome! Please ensure:
1. Type hints for all function signatures
2. Docstrings for public APIs
3. Exception handling with custom exceptions
4. Backward compatibility with existing APIs

## License

[Your License Here]

## Author

YQ

## Acknowledgments

Built with [Anthropic Claude API](https://www.anthropic.com/)

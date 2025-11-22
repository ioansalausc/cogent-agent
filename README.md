# Cogent Agent

Docker-containerized AI Agent built on the Claude Agent SDK with NATS communication.

## Features

- **Claude Agent SDK Integration**: Full access to Claude Code capabilities
- **NATS Communication**: Request-reply commands and pub-sub events
- **JetStream Persistence**: Reliable message delivery and replay
- **KV State Store**: Persistent agent state
- **GitHub Integration**: Auto-branching, commits, PRs, and CI/CD
- **Hooks System**: Event publishing to NATS for external monitoring
- **Self-Modifying Skills**: PR-based workflow for skill updates
- **Interactive CLI**: REPL mode with streaming responses

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Claude API credentials (OAuth token or API key)
- GitHub personal access token (for Git operations)

### Setup

1. Clone the repository:
   ```bash
   cd cogent-agent
   ```

2. Copy environment template:
   ```bash
   cp .env.example .env
   ```

3. Configure credentials in `.env`:
   ```bash
   # Required - at least one
   CLAUDE_CODE_OAUTH_TOKEN=your_oauth_token
   # or
   ANTHROPIC_API_KEY=sk-ant-...

   # For GitHub operations
   GH_TOKEN=ghp_...
   ```

4. Start the services:
   ```bash
   docker-compose up -d
   ```

5. Run the CLI:
   ```bash
   docker-compose run --rm cli
   ```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Docker Network                          │
│                                                              │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐ │
│  │   CLI/Web    │     │    NATS      │     │   Cogent     │ │
│  │   Client     │────▶│   Server     │◀────│   Agent      │ │
│  └──────────────┘     │  (JetStream) │     │  (Claude)    │ │
│                       └──────────────┘     └──────────────┘ │
│                              │                    │          │
│                              ▼                    ▼          │
│                       ┌──────────────┐    ┌──────────────┐  │
│                       │   KV Store   │    │  Workspace   │  │
│                       │   (State)    │    │  (Projects)  │  │
│                       └──────────────┘    └──────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## CLI Usage

### Interactive Mode (REPL)

```bash
# Start the CLI
cogent

# Or via Docker
docker-compose run --rm cli
```

Commands in REPL:
- `/status` - Show agent status
- `/cancel` - Cancel current task
- `/help` - Show help
- `/exit` - Exit

### Single Command Mode

```bash
# Execute a single task
cogent run "Create a Python function that calculates fibonacci"

# Get agent status
cogent status
```

## NATS Subjects

| Subject | Type | Purpose |
|---------|------|---------|
| `agent.{id}.command` | Request-Reply | Send commands to agent |
| `agent.{id}.events.*` | Pub-Sub | Agent events (progress, status) |
| `agent.{id}.status` | Pub-Sub | Health/heartbeat |
| `agent.shared.broadcast` | Pub-Sub | Cross-agent messages |

## Hooks

The agent publishes events to NATS via hooks:

- `PreToolUse` - Before tool execution (can block)
- `PostToolUse` - After tool completion
- `SessionStart` - Session initialization
- `Notification` - Agent notifications
- `Stop` - Agent stopping

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CLAUDE_CODE_OAUTH_TOKEN` | OAuth token for Claude Max | - |
| `ANTHROPIC_API_KEY` | Anthropic API key (fallback) | - |
| `GH_TOKEN` | GitHub personal access token | - |
| `AGENT_ID` | Unique agent identifier | `cogent-agent-001` |
| `NATS_URL` | NATS server URL | `nats://nats:4222` |
| `LOG_LEVEL` | Logging level | `INFO` |

### Skills & Commands

Place custom skills in `assets/skills/` and commands in `assets/commands/`:

```
assets/
├── CLAUDE.md          # Global agent instructions
├── skills/
│   └── my-skill/
│       ├── SKILL.md   # Skill definition
│       └── scripts/   # Optional scripts
└── commands/
    └── my-command.md  # Slash command
```

## Development

### Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black src/
ruff check src/
```

### Build Docker Image

```bash
docker-compose build
```

## Future Roadmap

- [ ] Orchestrator agent for multi-project management
- [ ] WebSocket gateway for web clients
- [ ] Slack/Discord integrations
- [ ] Multi-tenant support
- [ ] Horizontal scaling

## License

MIT

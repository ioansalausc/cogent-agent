# Cogent Agent - Docker Container for Claude Agent SDK
# Multi-stage build for optimized image size

# =============================================================================
# Stage 1: Base image with system dependencies
# =============================================================================
FROM python:3.11-slim-bookworm AS base

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Node.js prerequisites
    curl \
    gnupg \
    # Git and GitHub CLI
    git \
    # Build tools for native dependencies
    build-essential \
    # Network utilities
    ca-certificates \
    # Process management
    tini \
    # JSON processing for hooks
    jq \
    # Archive extraction
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22.x
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI (required by claude-agent-sdk)
RUN npm install -g @anthropic-ai/claude-code

# Install GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# =============================================================================
# Stage 2: Python dependencies
# =============================================================================
FROM base AS python-deps

WORKDIR /build

# Copy requirements first for better caching
COPY requirements.txt .

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt

# =============================================================================
# Stage 3: Final runtime image
# =============================================================================
FROM base AS runtime

# Create non-root user for security
RUN groupadd --gid 1000 cogent \
    && useradd --uid 1000 --gid cogent --shell /bin/bash --create-home cogent

# Copy virtual environment from builder
COPY --from=python-deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create directory structure
RUN mkdir -p /workspace /assets /home/cogent/.claude /app \
    && chown -R cogent:cogent /workspace /assets /home/cogent /app

WORKDIR /app

# Copy application code
COPY --chown=cogent:cogent src/ ./src/
COPY --chown=cogent:cogent config/ ./config/
COPY --chown=cogent:cogent hooks/ ./hooks/
COPY --chown=cogent:cogent scripts/ ./scripts/

# Make hook scripts executable
RUN chmod +x ./hooks/*.sh 2>/dev/null || true \
    && chmod +x ./scripts/*.sh 2>/dev/null || true

# Install NATS CLI for hook scripts
RUN curl -fsSL https://github.com/nats-io/natscli/releases/download/v0.3.0/nats-0.3.0-linux-amd64.zip -o nats.zip \
    && unzip nats.zip -d /tmp \
    && mv /tmp/nats-0.3.0-linux-amd64/nats /usr/local/bin/ \
    && rm -rf nats.zip /tmp/nats-*

# Switch to non-root user
USER cogent

# Environment variables for Claude Agent SDK
ENV CLAUDE_CODE_OAUTH_TOKEN="" \
    ANTHROPIC_API_KEY="" \
    NATS_URL="nats://nats:4222" \
    AGENT_ID="cogent-agent-001" \
    WORKSPACE_DIR="/workspace" \
    ASSETS_DIR="/assets" \
    LOG_LEVEL="INFO" \
    # Claude Code specific
    CLAUDE_CODE_SKIP_PERMISSIONS="true"

# Volumes
VOLUME ["/workspace", "/assets", "/home/cogent/.claude"]

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import asyncio; from src.agent.health import check_health; asyncio.run(check_health())" || exit 1

# Use tini as init system for proper signal handling
ENTRYPOINT ["/usr/bin/tini", "--", "/app/scripts/entrypoint.sh"]

# Default command - run the agent
CMD ["python", "-m", "src.agent.main"]

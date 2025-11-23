"""
Configuration management for Cogent Agent.

Uses Pydantic settings to load configuration from environment variables
with sensible defaults and validation.
"""

from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthConfig(BaseSettings):
    """Authentication configuration."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # Claude authentication
    claude_code_oauth_token: Optional[str] = Field(
        default=None,
        description="OAuth token for Claude Max subscription",
    )
    anthropic_api_key: Optional[str] = Field(
        default=None,
        description="Anthropic API key (fallback if OAuth fails)",
    )

    # GitHub authentication
    gh_token: Optional[str] = Field(
        default=None,
        description="GitHub personal access token",
    )

    @field_validator("claude_code_oauth_token", "anthropic_api_key", "gh_token", mode="before")
    @classmethod
    def empty_string_to_none(cls, v: Optional[str]) -> Optional[str]:
        """Convert empty strings to None."""
        if v == "":
            return None
        return v

    @property
    def has_claude_auth(self) -> bool:
        """Check if any Claude authentication is configured."""
        return bool(self.claude_code_oauth_token or self.anthropic_api_key)

    @property
    def preferred_auth_method(self) -> Optional[str]:
        """Return the preferred authentication method."""
        if self.claude_code_oauth_token:
            return "oauth"
        if self.anthropic_api_key:
            return "api_key"
        return None


class NATSConfig(BaseSettings):
    """NATS messaging configuration."""

    model_config = SettingsConfigDict(env_prefix="NATS_", extra="ignore")

    url: str = Field(
        default="nats://localhost:4222",
        description="NATS server URL",
    )
    connect_timeout: float = Field(
        default=10.0,
        description="Connection timeout in seconds",
    )
    reconnect_time_wait: float = Field(
        default=2.0,
        description="Time to wait between reconnection attempts",
    )
    max_reconnect_attempts: int = Field(
        default=60,
        description="Maximum number of reconnection attempts",
    )


class GitConfig(BaseSettings):
    """
    Git configuration.

    Environment variables:
        GIT_AUTHOR_NAME: Git author name for commits
        GIT_AUTHOR_EMAIL: Git author email for commits
        GIT_DEFAULT_BRANCH: Default branch name (default: main)
        GIT_AUTO_COMMIT_INTERVAL: Auto-commit interval in seconds (0 to disable)
    """

    model_config = SettingsConfigDict(env_prefix="GIT_", extra="ignore")

    author_name: str = Field(
        default="Cogent Agent",
        description="Git author name for commits",
    )
    author_email: str = Field(
        default="agent@cogent.local",
        description="Git author email for commits",
    )
    default_branch: str = Field(
        default="main",
        description="Default branch name",
    )
    auto_commit_interval: int = Field(
        default=300,
        description="Auto-commit interval in seconds (0 to disable)",
    )


class AgentConfig(BaseSettings):
    """Main agent configuration."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # Agent identity
    agent_id: str = Field(
        default="cogent-agent-001",
        description="Unique identifier for this agent",
    )

    # Paths
    workspace_dir: Path = Field(
        default=Path("/workspace"),
        description="Root directory for project workspaces",
    )
    assets_dir: Path = Field(
        default=Path("/assets"),
        description="Directory for skills, commands, and CLAUDE.md",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )

    # Claude Code settings
    claude_code_skip_permissions: bool = Field(
        default=True,
        description="Skip permission prompts for headless operation",
    )

    # Sub-configurations
    auth: AuthConfig = Field(default_factory=AuthConfig)
    nats: NATSConfig = Field(default_factory=NATSConfig)
    git: GitConfig = Field(default_factory=GitConfig)

    @field_validator("workspace_dir", "assets_dir", mode="before")
    @classmethod
    def ensure_path(cls, v):
        """Ensure value is a Path object."""
        if isinstance(v, str):
            return Path(v)
        return v


def load_config() -> AgentConfig:
    """Load configuration from environment variables."""
    return AgentConfig(
        auth=AuthConfig(),
        nats=NATSConfig(),
        git=GitConfig(),
    )


# Global configuration instance (lazy-loaded)
_config: Optional[AgentConfig] = None


def get_config() -> AgentConfig:
    """Get or create the global configuration instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config

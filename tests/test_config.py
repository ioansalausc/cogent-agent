"""
Unit tests for the config module.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.agent.config import (
    AgentConfig,
    AuthConfig,
    GitConfig,
    NATSConfig,
    get_config,
    load_config,
)


class TestAuthConfig:
    """Tests for AuthConfig."""

    def test_default_values(self):
        """Test that default values are None."""
        config = AuthConfig()
        assert config.claude_code_oauth_token is None
        assert config.anthropic_api_key is None
        assert config.gh_token is None

    def test_has_claude_auth_with_oauth(self):
        """Test has_claude_auth with OAuth token."""
        config = AuthConfig(claude_code_oauth_token="test_token_12345678901234567890")
        assert config.has_claude_auth is True

    def test_has_claude_auth_with_api_key(self):
        """Test has_claude_auth with API key."""
        config = AuthConfig(anthropic_api_key="sk-ant-test-key")
        assert config.has_claude_auth is True

    def test_has_claude_auth_with_none(self):
        """Test has_claude_auth without any auth."""
        config = AuthConfig()
        assert config.has_claude_auth is False

    def test_preferred_auth_method_oauth(self):
        """Test preferred_auth_method returns oauth when available."""
        config = AuthConfig(
            claude_code_oauth_token="test_token_12345678901234567890",
            anthropic_api_key="sk-ant-test-key",
        )
        assert config.preferred_auth_method == "oauth"

    def test_preferred_auth_method_api_key(self):
        """Test preferred_auth_method returns api_key when no oauth."""
        config = AuthConfig(anthropic_api_key="sk-ant-test-key")
        assert config.preferred_auth_method == "api_key"

    def test_preferred_auth_method_none(self):
        """Test preferred_auth_method returns None when no auth."""
        config = AuthConfig()
        assert config.preferred_auth_method is None

    def test_empty_string_to_none_validator(self):
        """Test that empty strings are converted to None."""
        config = AuthConfig(
            claude_code_oauth_token="",
            anthropic_api_key="",
            gh_token="",
        )
        assert config.claude_code_oauth_token is None
        assert config.anthropic_api_key is None
        assert config.gh_token is None


class TestNATSConfig:
    """Tests for NATSConfig."""

    def test_default_values(self):
        """Test default NATS configuration values."""
        config = NATSConfig()
        assert config.url == "nats://localhost:4222"
        assert config.connect_timeout == 10.0
        assert config.reconnect_time_wait == 2.0
        assert config.max_reconnect_attempts == 60

    def test_custom_values(self):
        """Test custom NATS configuration."""
        config = NATSConfig(
            url="nats://custom:4333",
            connect_timeout=5.0,
            reconnect_time_wait=1.0,
            max_reconnect_attempts=10,
        )
        assert config.url == "nats://custom:4333"
        assert config.connect_timeout == 5.0
        assert config.reconnect_time_wait == 1.0
        assert config.max_reconnect_attempts == 10


class TestGitConfig:
    """Tests for GitConfig."""

    def test_default_values(self):
        """Test default Git configuration values."""
        config = GitConfig()
        assert config.author_name == "Cogent Agent"
        assert config.author_email == "cogent@localhost"
        assert config.default_branch == "main"
        assert config.auto_commit_interval == 300

    def test_custom_values(self):
        """Test custom Git configuration."""
        config = GitConfig(
            author_name="Test Author",
            author_email="test@example.com",
            default_branch="develop",
            auto_commit_interval=600,
        )
        assert config.author_name == "Test Author"
        assert config.author_email == "test@example.com"
        assert config.default_branch == "develop"
        assert config.auto_commit_interval == 600


class TestAgentConfig:
    """Tests for AgentConfig."""

    def test_default_values(self):
        """Test default agent configuration values."""
        config = AgentConfig()
        assert config.agent_id == "cogent-agent-001"
        assert config.workspace_dir == Path("/workspace")
        assert config.assets_dir == Path("/assets")
        assert config.log_level == "INFO"
        assert config.claude_code_skip_permissions is True

    def test_custom_values(self):
        """Test custom agent configuration."""
        config = AgentConfig(
            agent_id="test-agent",
            workspace_dir=Path("/test/workspace"),
            assets_dir=Path("/test/assets"),
            log_level="DEBUG",
            claude_code_skip_permissions=False,
        )
        assert config.agent_id == "test-agent"
        assert config.workspace_dir == Path("/test/workspace")
        assert config.assets_dir == Path("/test/assets")
        assert config.log_level == "DEBUG"
        assert config.claude_code_skip_permissions is False

    def test_path_validator_string_conversion(self):
        """Test that string paths are converted to Path objects."""
        config = AgentConfig(
            workspace_dir="/string/path/workspace",
            assets_dir="/string/path/assets",
        )
        assert isinstance(config.workspace_dir, Path)
        assert isinstance(config.assets_dir, Path)
        assert config.workspace_dir == Path("/string/path/workspace")

    def test_nested_configs(self):
        """Test that nested configs are properly initialized."""
        config = AgentConfig()
        assert isinstance(config.auth, AuthConfig)
        assert isinstance(config.nats, NATSConfig)
        assert isinstance(config.git, GitConfig)


class TestLoadConfig:
    """Tests for load_config and get_config functions."""

    def test_load_config_returns_agent_config(self):
        """Test that load_config returns AgentConfig instance."""
        config = load_config()
        assert isinstance(config, AgentConfig)

    def test_load_config_from_env(self):
        """Test loading config from environment variables."""
        with patch.dict(
            os.environ,
            {
                "AGENT_ID": "env-agent-001",
                "LOG_LEVEL": "DEBUG",
            },
        ):
            config = load_config()
            assert config.agent_id == "env-agent-001"
            assert config.log_level == "DEBUG"

    def test_get_config_singleton(self):
        """Test that get_config returns the same instance."""
        # Reset global config
        import src.agent.config as config_module

        config_module._config = None

        config1 = get_config()
        config2 = get_config()
        assert config1 is config2

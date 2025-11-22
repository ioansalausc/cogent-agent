"""
Unit tests for the auth module.
"""

import os
from unittest.mock import patch

import pytest

from src.agent.auth import AuthManager, AuthMethod, AuthStatus
from src.agent.config import AuthConfig


class TestAuthMethod:
    """Tests for AuthMethod enum."""

    def test_auth_method_values(self):
        """Test AuthMethod enum values."""
        assert AuthMethod.OAUTH.value == "oauth"
        assert AuthMethod.API_KEY.value == "api_key"
        assert AuthMethod.NONE.value == "none"


class TestAuthStatus:
    """Tests for AuthStatus dataclass."""

    def test_auth_status_creation(self):
        """Test AuthStatus creation."""
        status = AuthStatus(
            method=AuthMethod.OAUTH,
            is_valid=True,
            message="Test message",
            token_preview="test...view",
        )
        assert status.method == AuthMethod.OAUTH
        assert status.is_valid is True
        assert status.message == "Test message"
        assert status.token_preview == "test...view"

    def test_auth_status_optional_token_preview(self):
        """Test AuthStatus with optional token_preview."""
        status = AuthStatus(
            method=AuthMethod.NONE,
            is_valid=False,
            message="No auth",
        )
        assert status.token_preview is None


class TestAuthManager:
    """Tests for AuthManager."""

    def test_init_default_config(self):
        """Test AuthManager initialization with default config."""
        config = AuthConfig()
        manager = AuthManager(config)
        assert manager.config == config
        assert manager.current_method == AuthMethod.NONE
        assert manager.is_authenticated is False

    def test_mask_token_short(self):
        """Test token masking for short tokens."""
        config = AuthConfig()
        manager = AuthManager(config)
        masked = manager._mask_token("12345678")
        assert masked == "********"

    def test_mask_token_long(self):
        """Test token masking for long tokens."""
        config = AuthConfig()
        manager = AuthManager(config)
        masked = manager._mask_token("abcdefghijklmnopqrstuvwxyz")
        assert masked == "abcd...wxyz"

    def test_mask_token_custom_visible(self):
        """Test token masking with custom visible chars."""
        config = AuthConfig()
        manager = AuthManager(config)
        masked = manager._mask_token("abcdefghijklmnopqrstuvwxyz", visible_chars=6)
        assert masked == "abcdef...uvwxyz"

    def test_validate_token_format_api_key_valid(self):
        """Test API key format validation - valid."""
        config = AuthConfig()
        manager = AuthManager(config)
        assert manager._validate_token_format("sk-ant-abc123456789", "api_key") is True
        assert manager._validate_token_format("sk-abc123456789", "api_key") is True

    def test_validate_token_format_api_key_invalid(self):
        """Test API key format validation - invalid."""
        config = AuthConfig()
        manager = AuthManager(config)
        assert manager._validate_token_format("invalid-key", "api_key") is False
        assert manager._validate_token_format("short", "api_key") is False

    def test_validate_token_format_oauth_valid(self):
        """Test OAuth token format validation - valid."""
        config = AuthConfig()
        manager = AuthManager(config)
        assert manager._validate_token_format("a" * 20, "oauth") is True
        assert manager._validate_token_format("a" * 100, "oauth") is True

    def test_validate_token_format_oauth_invalid(self):
        """Test OAuth token format validation - invalid."""
        config = AuthConfig()
        manager = AuthManager(config)
        assert manager._validate_token_format("short", "oauth") is False
        assert manager._validate_token_format("", "oauth") is False

    @pytest.mark.asyncio
    async def test_initialize_no_auth(self):
        """Test initialization with no auth configured."""
        config = AuthConfig()
        manager = AuthManager(config)
        status = await manager.initialize()

        assert status.is_valid is False
        assert status.method == AuthMethod.NONE
        assert "No valid authentication" in status.message

    @pytest.mark.asyncio
    async def test_initialize_oauth_valid(self):
        """Test initialization with valid OAuth token."""
        token = "test_oauth_token_12345678901234567890"
        config = AuthConfig(claude_code_oauth_token=token)
        manager = AuthManager(config)

        # Clean environment
        with patch.dict(os.environ, {}, clear=True):
            status = await manager.initialize()

            assert status.is_valid is True
            assert status.method == AuthMethod.OAUTH
            assert manager.current_method == AuthMethod.OAUTH
            assert os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == token

    @pytest.mark.asyncio
    async def test_initialize_api_key_valid(self):
        """Test initialization with valid API key."""
        api_key = "sk-ant-test-api-key-1234567890"
        config = AuthConfig(anthropic_api_key=api_key)
        manager = AuthManager(config)

        # Clean environment
        with patch.dict(os.environ, {}, clear=True):
            status = await manager.initialize()

            assert status.is_valid is True
            assert status.method == AuthMethod.API_KEY
            assert manager.current_method == AuthMethod.API_KEY
            assert os.environ.get("ANTHROPIC_API_KEY") == api_key

    @pytest.mark.asyncio
    async def test_initialize_oauth_fallback_to_api_key(self):
        """Test fallback from invalid OAuth to API key."""
        config = AuthConfig(
            claude_code_oauth_token="short",  # Invalid OAuth
            anthropic_api_key="sk-ant-valid-api-key-12345",  # Valid API key
        )
        manager = AuthManager(config)

        with patch.dict(os.environ, {}, clear=True):
            status = await manager.initialize()

            assert status.is_valid is True
            assert status.method == AuthMethod.API_KEY

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self):
        """Test that initialize is idempotent."""
        token = "test_oauth_token_12345678901234567890"
        config = AuthConfig(claude_code_oauth_token=token)
        manager = AuthManager(config)

        with patch.dict(os.environ, {}, clear=True):
            status1 = await manager.initialize()
            status2 = await manager.initialize()

            assert status1.is_valid == status2.is_valid
            assert status1.method == status2.method

    @pytest.mark.asyncio
    async def test_refresh_reinitializes(self):
        """Test that refresh re-initializes authentication."""
        token = "test_oauth_token_12345678901234567890"
        config = AuthConfig(claude_code_oauth_token=token)
        manager = AuthManager(config)

        with patch.dict(os.environ, {}, clear=True):
            await manager.initialize()
            assert manager._is_initialized is True

            await manager.refresh()
            assert manager._is_initialized is True  # Still initialized after refresh

    def test_get_sdk_auth_env_oauth(self):
        """Test get_sdk_auth_env with OAuth."""
        config = AuthConfig()
        manager = AuthManager(config)
        manager._current_method = AuthMethod.OAUTH

        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "test_token"}):
            env = manager.get_sdk_auth_env()
            assert env == {"CLAUDE_CODE_OAUTH_TOKEN": "test_token"}

    def test_get_sdk_auth_env_api_key(self):
        """Test get_sdk_auth_env with API key."""
        config = AuthConfig()
        manager = AuthManager(config)
        manager._current_method = AuthMethod.API_KEY

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            env = manager.get_sdk_auth_env()
            assert env == {"ANTHROPIC_API_KEY": "sk-ant-test"}

    def test_get_sdk_auth_env_none(self):
        """Test get_sdk_auth_env with no auth."""
        config = AuthConfig()
        manager = AuthManager(config)
        manager._current_method = AuthMethod.NONE

        env = manager.get_sdk_auth_env()
        assert env == {}

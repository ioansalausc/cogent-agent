"""
Authentication module for Cogent Agent.

Handles OAuth token and API key authentication for Claude Agent SDK.
Provides validation, refresh handling, and fallback mechanisms.
"""

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import structlog

from .config import AuthConfig, get_config

logger = structlog.get_logger(__name__)


class AuthMethod(Enum):
    """Supported authentication methods."""

    OAUTH = "oauth"
    API_KEY = "api_key"
    NONE = "none"


@dataclass
class AuthStatus:
    """Authentication status information."""

    method: AuthMethod
    is_valid: bool
    message: str
    token_preview: Optional[str] = None  # First/last few chars for debugging


class AuthManager:
    """
    Manages authentication for the Claude Agent SDK.

    Supports OAuth tokens (preferred) with fallback to API keys.
    Sets environment variables expected by the SDK.
    """

    def __init__(self, config: Optional[AuthConfig] = None):
        """
        Initialize the authentication manager.

        Args:
            config: Authentication configuration. If None, loads from environment.
        """
        self.config = config or get_config().auth
        self._current_method: AuthMethod = AuthMethod.NONE
        self._is_initialized = False

    @property
    def current_method(self) -> AuthMethod:
        """Get the current authentication method in use."""
        return self._current_method

    @property
    def is_authenticated(self) -> bool:
        """Check if authentication is configured and valid."""
        return self._current_method != AuthMethod.NONE

    def _mask_token(self, token: str, visible_chars: int = 4) -> str:
        """Mask a token for safe logging."""
        if len(token) <= visible_chars * 2:
            return "*" * len(token)
        return f"{token[:visible_chars]}...{token[-visible_chars:]}"

    async def initialize(self) -> AuthStatus:
        """
        Initialize authentication by validating and setting credentials.

        Tries OAuth first, then falls back to API key if OAuth fails.

        Returns:
            AuthStatus with the result of initialization.
        """
        if self._is_initialized:
            logger.debug("Auth already initialized", method=self._current_method.value)
            return self._get_current_status()

        # Try OAuth first (preferred)
        if self.config.claude_code_oauth_token:
            status = await self._try_oauth()
            if status.is_valid:
                self._is_initialized = True
                return status
            logger.warning(
                "OAuth authentication failed, trying API key fallback",
                reason=status.message,
            )

        # Fall back to API key
        if self.config.anthropic_api_key:
            status = await self._try_api_key()
            if status.is_valid:
                self._is_initialized = True
                return status

        # No valid authentication
        self._current_method = AuthMethod.NONE
        return AuthStatus(
            method=AuthMethod.NONE,
            is_valid=False,
            message="No valid authentication configured. Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY.",
        )

    async def _try_oauth(self) -> AuthStatus:
        """
        Try to authenticate using OAuth token.

        Sets CLAUDE_CODE_OAUTH_TOKEN environment variable for the SDK.
        """
        token = self.config.claude_code_oauth_token
        if not token:
            return AuthStatus(
                method=AuthMethod.OAUTH,
                is_valid=False,
                message="OAuth token not configured",
            )

        # Basic validation - check token format
        # OAuth tokens typically have a specific format/prefix
        if not self._validate_token_format(token, "oauth"):
            return AuthStatus(
                method=AuthMethod.OAUTH,
                is_valid=False,
                message="OAuth token format appears invalid",
                token_preview=self._mask_token(token),
            )

        # Set environment variable for Claude Agent SDK
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token

        # Clear API key to avoid conflicts
        if "ANTHROPIC_API_KEY" in os.environ:
            del os.environ["ANTHROPIC_API_KEY"]

        self._current_method = AuthMethod.OAUTH

        logger.info(
            "OAuth authentication configured",
            token_preview=self._mask_token(token),
        )

        return AuthStatus(
            method=AuthMethod.OAUTH,
            is_valid=True,
            message="OAuth authentication configured successfully",
            token_preview=self._mask_token(token),
        )

    async def _try_api_key(self) -> AuthStatus:
        """
        Try to authenticate using Anthropic API key.

        Sets ANTHROPIC_API_KEY environment variable for the SDK.
        """
        api_key = self.config.anthropic_api_key
        if not api_key:
            return AuthStatus(
                method=AuthMethod.API_KEY,
                is_valid=False,
                message="API key not configured",
            )

        # Basic validation - Anthropic API keys start with "sk-ant-"
        if not self._validate_token_format(api_key, "api_key"):
            return AuthStatus(
                method=AuthMethod.API_KEY,
                is_valid=False,
                message="API key format appears invalid (expected 'sk-ant-' prefix)",
                token_preview=self._mask_token(api_key),
            )

        # Set environment variable for Claude Agent SDK
        os.environ["ANTHROPIC_API_KEY"] = api_key

        # Clear OAuth token to avoid conflicts
        if "CLAUDE_CODE_OAUTH_TOKEN" in os.environ:
            del os.environ["CLAUDE_CODE_OAUTH_TOKEN"]

        self._current_method = AuthMethod.API_KEY

        logger.info(
            "API key authentication configured",
            token_preview=self._mask_token(api_key),
        )

        return AuthStatus(
            method=AuthMethod.API_KEY,
            is_valid=True,
            message="API key authentication configured successfully",
            token_preview=self._mask_token(api_key),
        )

    def _validate_token_format(self, token: str, token_type: str) -> bool:
        """
        Validate token format based on type.

        Args:
            token: The token to validate.
            token_type: Type of token ("oauth" or "api_key").

        Returns:
            True if format appears valid, False otherwise.
        """
        if not token or len(token) < 10:
            return False

        if token_type == "api_key":
            # Anthropic API keys typically start with "sk-ant-"
            return token.startswith("sk-ant-") or token.startswith("sk-")

        # OAuth tokens - just check minimum length and no obvious issues
        return len(token) >= 20

    def _get_current_status(self) -> AuthStatus:
        """Get the current authentication status."""
        if self._current_method == AuthMethod.OAUTH:
            token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
            return AuthStatus(
                method=AuthMethod.OAUTH,
                is_valid=True,
                message="OAuth authentication active",
                token_preview=self._mask_token(token) if token else None,
            )
        elif self._current_method == AuthMethod.API_KEY:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            return AuthStatus(
                method=AuthMethod.API_KEY,
                is_valid=True,
                message="API key authentication active",
                token_preview=self._mask_token(api_key) if api_key else None,
            )
        else:
            return AuthStatus(
                method=AuthMethod.NONE,
                is_valid=False,
                message="No authentication configured",
            )

    async def refresh(self) -> AuthStatus:
        """
        Refresh authentication if needed.

        Currently just re-initializes. Future: implement actual token refresh.
        """
        self._is_initialized = False
        return await self.initialize()

    def get_sdk_auth_env(self) -> dict[str, str]:
        """
        Get environment variables to pass to SDK subprocesses.

        Returns:
            Dictionary of environment variables for authentication.
        """
        env = {}

        if self._current_method == AuthMethod.OAUTH:
            token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
            if token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        elif self._current_method == AuthMethod.API_KEY:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key

        return env


# Global auth manager instance
_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    """Get or create the global authentication manager."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager

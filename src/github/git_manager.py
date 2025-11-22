"""
Git repository management for Cogent Agent.

Handles:
- Repository initialization and configuration
- Branch creation and management
- Commit operations with auto-staging
- Remote operations (push, pull)
"""

import asyncio
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class GitStatus:
    """Git repository status."""

    is_repo: bool
    branch: str
    has_remote: bool
    remote_url: Optional[str]
    is_clean: bool
    staged_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    ahead: int = 0
    behind: int = 0


@dataclass
class CommitInfo:
    """Information about a commit."""

    sha: str
    short_sha: str
    message: str
    author: str
    timestamp: str


class GitManager:
    """
    Manages Git operations for a project directory.

    Provides high-level operations for:
    - Repository initialization
    - Branch management
    - Commit workflows
    - Remote synchronization
    """

    def __init__(
        self,
        project_dir: Path,
        author_name: str = "Cogent Agent",
        author_email: str = "cogent@localhost",
        default_branch: str = "main",
    ):
        """
        Initialize Git manager.

        Args:
            project_dir: Path to the project directory.
            author_name: Git author name for commits.
            author_email: Git author email for commits.
            default_branch: Default branch name.
        """
        self.project_dir = Path(project_dir)
        self.author_name = author_name
        self.author_email = author_email
        self.default_branch = default_branch

    async def _run_git(
        self,
        *args: str,
        check: bool = True,
        capture_output: bool = True,
    ) -> tuple[int, str, str]:
        """
        Run a git command.

        Args:
            *args: Git command arguments.
            check: Raise exception on non-zero exit.
            capture_output: Capture stdout/stderr.

        Returns:
            Tuple of (return_code, stdout, stderr).
        """
        cmd = ["git", "-C", str(self.project_dir)] + list(args)

        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = self.author_name
        env["GIT_AUTHOR_EMAIL"] = self.author_email
        env["GIT_COMMITTER_NAME"] = self.author_name
        env["GIT_COMMITTER_EMAIL"] = self.author_email

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE if capture_output else None,
            stderr=asyncio.subprocess.PIPE if capture_output else None,
            env=env,
        )

        stdout, stderr = await process.communicate()
        stdout_str = stdout.decode().strip() if stdout else ""
        stderr_str = stderr.decode().strip() if stderr else ""

        if check and process.returncode != 0:
            raise GitError(f"Git command failed: {' '.join(args)}\n{stderr_str}")

        return process.returncode, stdout_str, stderr_str

    async def is_git_repo(self) -> bool:
        """Check if the project directory is a Git repository."""
        try:
            code, _, _ = await self._run_git("rev-parse", "--git-dir", check=False)
            return code == 0
        except Exception:
            return False

    async def init_repo(self, initial_branch: Optional[str] = None) -> bool:
        """
        Initialize a new Git repository.

        Args:
            initial_branch: Initial branch name (default: self.default_branch).

        Returns:
            True if repository was created, False if already exists.
        """
        if await self.is_git_repo():
            logger.debug("Repository already exists", path=str(self.project_dir))
            return False

        branch = initial_branch or self.default_branch
        await self._run_git("init", "-b", branch)

        # Create initial .gitignore if not exists
        gitignore_path = self.project_dir / ".gitignore"
        if not gitignore_path.exists():
            gitignore_path.write_text(
                "# Python\n__pycache__/\n*.pyc\n.env\nvenv/\n\n"
                "# IDE\n.idea/\n.vscode/\n*.swp\n\n"
                "# OS\n.DS_Store\n"
            )

        logger.info("Initialized Git repository", path=str(self.project_dir), branch=branch)
        return True

    async def get_status(self) -> GitStatus:
        """Get comprehensive repository status."""
        if not await self.is_git_repo():
            return GitStatus(
                is_repo=False,
                branch="",
                has_remote=False,
                remote_url=None,
                is_clean=True,
            )

        # Get current branch
        _, branch, _ = await self._run_git("rev-parse", "--abbrev-ref", "HEAD")

        # Get remote URL
        has_remote = False
        remote_url = None
        try:
            _, remote_url, _ = await self._run_git("remote", "get-url", "origin", check=False)
            has_remote = bool(remote_url)
        except Exception:
            pass

        # Get file status
        _, status_output, _ = await self._run_git("status", "--porcelain")

        staged_files = []
        modified_files = []
        untracked_files = []

        for line in status_output.split("\n"):
            if not line:
                continue
            status = line[:2]
            filename = line[3:]

            if status[0] in "MADRC":
                staged_files.append(filename)
            if status[1] in "MD":
                modified_files.append(filename)
            if status == "??":
                untracked_files.append(filename)

        is_clean = not (staged_files or modified_files or untracked_files)

        # Get ahead/behind counts
        ahead = behind = 0
        if has_remote:
            try:
                _, ab_output, _ = await self._run_git(
                    "rev-list", "--left-right", "--count", f"HEAD...origin/{branch}",
                    check=False,
                )
                if ab_output:
                    parts = ab_output.split()
                    if len(parts) == 2:
                        ahead, behind = int(parts[0]), int(parts[1])
            except Exception:
                pass

        return GitStatus(
            is_repo=True,
            branch=branch,
            has_remote=has_remote,
            remote_url=remote_url,
            is_clean=is_clean,
            staged_files=staged_files,
            modified_files=modified_files,
            untracked_files=untracked_files,
            ahead=ahead,
            behind=behind,
        )

    async def create_branch(
        self,
        branch_name: str,
        from_branch: Optional[str] = None,
        checkout: bool = True,
    ) -> bool:
        """
        Create a new branch.

        Args:
            branch_name: Name of the new branch.
            from_branch: Branch to create from (default: current).
            checkout: Whether to checkout the new branch.

        Returns:
            True if branch was created.
        """
        # Ensure valid branch name
        branch_name = self._sanitize_branch_name(branch_name)

        if from_branch:
            await self._run_git("checkout", from_branch)

        if checkout:
            await self._run_git("checkout", "-b", branch_name)
        else:
            await self._run_git("branch", branch_name)

        logger.info("Created branch", branch=branch_name)
        return True

    async def checkout_branch(self, branch_name: str, create: bool = False) -> bool:
        """
        Checkout a branch.

        Args:
            branch_name: Branch to checkout.
            create: Create if doesn't exist.

        Returns:
            True if successful.
        """
        if create:
            # Check if branch exists
            code, _, _ = await self._run_git(
                "rev-parse", "--verify", branch_name, check=False
            )
            if code != 0:
                return await self.create_branch(branch_name)

        await self._run_git("checkout", branch_name)
        return True

    async def stage_files(self, files: Optional[list[str]] = None) -> list[str]:
        """
        Stage files for commit.

        Args:
            files: Specific files to stage. None = all modified/untracked.

        Returns:
            List of staged files.
        """
        if files:
            for file in files:
                await self._run_git("add", file)
            return files
        else:
            await self._run_git("add", "-A")
            status = await self.get_status()
            return status.staged_files

    async def commit(
        self,
        message: str,
        files: Optional[list[str]] = None,
        allow_empty: bool = False,
    ) -> Optional[CommitInfo]:
        """
        Create a commit.

        Args:
            message: Commit message.
            files: Specific files to commit. None = all staged.
            allow_empty: Allow empty commits.

        Returns:
            CommitInfo if successful, None if nothing to commit.
        """
        # Stage files if specified
        if files:
            await self.stage_files(files)

        # Check if there's anything to commit
        status = await self.get_status()
        if not status.staged_files and not allow_empty:
            logger.debug("Nothing to commit")
            return None

        # Build commit command
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")

        await self._run_git(*args)

        # Get commit info
        _, sha, _ = await self._run_git("rev-parse", "HEAD")
        _, short_sha, _ = await self._run_git("rev-parse", "--short", "HEAD")

        commit_info = CommitInfo(
            sha=sha,
            short_sha=short_sha,
            message=message.split("\n")[0],
            author=f"{self.author_name} <{self.author_email}>",
            timestamp=datetime.utcnow().isoformat(),
        )

        logger.info("Created commit", sha=short_sha, message=message[:50])
        return commit_info

    async def push(
        self,
        remote: str = "origin",
        branch: Optional[str] = None,
        set_upstream: bool = True,
        force: bool = False,
    ) -> bool:
        """
        Push to remote.

        Args:
            remote: Remote name.
            branch: Branch to push (default: current).
            set_upstream: Set upstream tracking.
            force: Force push.

        Returns:
            True if successful.
        """
        if not branch:
            status = await self.get_status()
            branch = status.branch

        args = ["push"]
        if set_upstream:
            args.extend(["-u", remote, branch])
        else:
            args.extend([remote, branch])

        if force:
            args.append("--force")

        await self._run_git(*args)
        logger.info("Pushed to remote", remote=remote, branch=branch)
        return True

    async def pull(
        self,
        remote: str = "origin",
        branch: Optional[str] = None,
        rebase: bool = False,
    ) -> bool:
        """
        Pull from remote.

        Args:
            remote: Remote name.
            branch: Branch to pull.
            rebase: Use rebase instead of merge.

        Returns:
            True if successful.
        """
        args = ["pull"]
        if rebase:
            args.append("--rebase")
        args.append(remote)
        if branch:
            args.append(branch)

        await self._run_git(*args)
        return True

    async def add_remote(self, name: str, url: str) -> bool:
        """Add a remote repository."""
        await self._run_git("remote", "add", name, url)
        logger.info("Added remote", name=name, url=url)
        return True

    async def get_recent_commits(self, count: int = 10) -> list[CommitInfo]:
        """Get recent commits."""
        _, output, _ = await self._run_git(
            "log", f"-{count}", "--format=%H|%h|%s|%an|%aI"
        )

        commits = []
        for line in output.split("\n"):
            if not line:
                continue
            parts = line.split("|", 4)
            if len(parts) == 5:
                commits.append(CommitInfo(
                    sha=parts[0],
                    short_sha=parts[1],
                    message=parts[2],
                    author=parts[3],
                    timestamp=parts[4],
                ))

        return commits

    async def get_diff(
        self,
        staged: bool = False,
        file: Optional[str] = None,
    ) -> str:
        """Get diff output."""
        args = ["diff"]
        if staged:
            args.append("--staged")
        if file:
            args.append(file)

        _, output, _ = await self._run_git(*args)
        return output

    def _sanitize_branch_name(self, name: str) -> str:
        """Sanitize a branch name to be Git-compatible."""
        # Replace spaces and invalid characters
        name = re.sub(r"[^\w\-./]", "-", name)
        # Remove leading/trailing dashes
        name = name.strip("-")
        # Collapse multiple dashes
        name = re.sub(r"-+", "-", name)
        return name


class GitError(Exception):
    """Git operation error."""

    pass

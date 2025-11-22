"""
CI/CD Workflow Manager for Cogent Agent.

Orchestrates the full development workflow:
1. Create feature branch
2. Make changes
3. Run tests
4. Commit and push
5. Create PR
6. Auto-merge on passing checks
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import structlog

from .git_manager import CommitInfo, GitManager, GitStatus
from .github_client import CheckStatus, GitHubClient, PRState, PullRequest
from .test_runner import TestResult, TestRunner

logger = structlog.get_logger(__name__)


class WorkflowState(Enum):
    """Workflow execution state."""

    IDLE = "idle"
    BRANCHING = "branching"
    DEVELOPING = "developing"
    TESTING = "testing"
    COMMITTING = "committing"
    PUSHING = "pushing"
    PR_CREATING = "pr_creating"
    WAITING_CHECKS = "waiting_checks"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class WorkflowConfig:
    """Configuration for the workflow."""

    # Branch settings
    branch_prefix: str = "feature"
    default_base_branch: str = "main"

    # Commit settings
    auto_commit_interval: int = 300  # seconds, 0 to disable
    commit_message_prefix: str = ""

    # Test settings
    run_tests_before_pr: bool = True
    test_timeout: int = 300

    # PR settings
    auto_create_pr: bool = True
    pr_draft: bool = False
    auto_merge: bool = True
    delete_branch_after_merge: bool = True

    # Reviewers and labels
    default_reviewers: list[str] = field(default_factory=list)
    default_labels: list[str] = field(default_factory=list)


@dataclass
class WorkflowResult:
    """Result of a workflow execution."""

    task_id: str
    success: bool
    state: WorkflowState
    branch: Optional[str] = None
    commits: list[CommitInfo] = field(default_factory=list)
    test_result: Optional[TestResult] = None
    pull_request: Optional[PullRequest] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkflowManager:
    """
    Manages the full development workflow for a project.

    Coordinates:
    - Git operations (branching, committing, pushing)
    - Test execution
    - GitHub operations (PRs, merging)
    """

    def __init__(
        self,
        project_dir: Path,
        config: Optional[WorkflowConfig] = None,
        git_author_name: str = "Cogent Agent",
        git_author_email: str = "cogent@localhost",
    ):
        """
        Initialize workflow manager.

        Args:
            project_dir: Project directory path.
            config: Workflow configuration.
            git_author_name: Git author name.
            git_author_email: Git author email.
        """
        self.project_dir = Path(project_dir)
        self.config = config or WorkflowConfig()

        # Initialize components
        self.git = GitManager(
            project_dir=self.project_dir,
            author_name=git_author_name,
            author_email=git_author_email,
            default_branch=self.config.default_base_branch,
        )
        self.github = GitHubClient(project_dir=self.project_dir)
        self.test_runner = TestRunner(project_dir=self.project_dir)

        # State tracking
        self._current_state = WorkflowState.IDLE
        self._current_task_id: Optional[str] = None
        self._event_callbacks: list[Callable] = []

        # Auto-commit tracking
        self._auto_commit_task: Optional[asyncio.Task] = None
        self._last_commit_time: Optional[datetime] = None

    @property
    def state(self) -> WorkflowState:
        """Get current workflow state."""
        return self._current_state

    def on_event(self, callback: Callable[[str, dict], None]) -> None:
        """Register callback for workflow events."""
        self._event_callbacks.append(callback)

    async def _emit_event(self, event: str, data: dict) -> None:
        """Emit a workflow event."""
        for callback in self._event_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event, data)
                else:
                    callback(event, data)
            except Exception as e:
                logger.error("Event callback failed", event=event, error=str(e))

    async def start_task(
        self,
        task_description: str,
        task_id: Optional[str] = None,
    ) -> WorkflowResult:
        """
        Start a new development task.

        Creates a feature branch and sets up for development.
        Ensures both Git repo and GitHub repo exist before starting.

        Args:
            task_description: Description of the task.
            task_id: Optional task ID (generated if not provided).

        Returns:
            WorkflowResult with branch info.
        """
        task_id = task_id or str(uuid.uuid4())[:8]
        self._current_task_id = task_id

        try:
            self._current_state = WorkflowState.BRANCHING
            await self._emit_event("workflow_started", {"task_id": task_id})

            # Ensure we have a git repo
            repo_created = await self.git.init_repo()
            if repo_created:
                logger.info("Initialized new Git repository")

            # Get current status
            status = await self.git.get_status()

            # Ensure GitHub repo exists and remote is configured
            if not status.has_remote:
                logger.info("No remote configured, creating GitHub repository")
                await self._ensure_github_repo()

            # Create feature branch
            branch_name = self._generate_branch_name(task_description, task_id)

            # Make sure we're on the base branch first
            if status.branch != self.config.default_base_branch:
                try:
                    await self.git.checkout_branch(self.config.default_base_branch)
                except Exception:
                    pass  # May not exist yet

            # Create and checkout feature branch
            await self.git.create_branch(branch_name)

            self._current_state = WorkflowState.DEVELOPING

            # Start auto-commit if configured
            if self.config.auto_commit_interval > 0:
                self._start_auto_commit()

            await self._emit_event("branch_created", {
                "task_id": task_id,
                "branch": branch_name,
            })

            return WorkflowResult(
                task_id=task_id,
                success=True,
                state=self._current_state,
                branch=branch_name,
            )

        except Exception as e:
            self._current_state = WorkflowState.FAILED
            logger.exception("Failed to start task")
            return WorkflowResult(
                task_id=task_id,
                success=False,
                state=self._current_state,
                error=str(e),
            )

    async def commit_changes(
        self,
        message: str,
        files: Optional[list[str]] = None,
    ) -> Optional[CommitInfo]:
        """
        Commit current changes.

        Args:
            message: Commit message.
            files: Specific files to commit (None = all).

        Returns:
            CommitInfo if committed, None if nothing to commit.
        """
        self._current_state = WorkflowState.COMMITTING

        # Add prefix if configured
        if self.config.commit_message_prefix:
            message = f"{self.config.commit_message_prefix} {message}"

        # Stage and commit
        await self.git.stage_files(files)
        commit = await self.git.commit(message)

        if commit:
            self._last_commit_time = datetime.utcnow()
            await self._emit_event("commit_created", {
                "sha": commit.short_sha,
                "message": commit.message,
            })

        self._current_state = WorkflowState.DEVELOPING
        return commit

    async def complete_task(
        self,
        pr_title: Optional[str] = None,
        pr_body: Optional[str] = None,
    ) -> WorkflowResult:
        """
        Complete the current task.

        Runs tests, creates PR, and sets up auto-merge.

        Args:
            pr_title: Pull request title.
            pr_body: Pull request body.

        Returns:
            WorkflowResult with final status.
        """
        task_id = self._current_task_id or "unknown"
        commits: list[CommitInfo] = []
        test_result: Optional[TestResult] = None
        pull_request: Optional[PullRequest] = None

        try:
            # Stop auto-commit
            self._stop_auto_commit()

            # Get current branch
            status = await self.git.get_status()
            branch = status.branch

            # Run tests if configured
            if self.config.run_tests_before_pr:
                self._current_state = WorkflowState.TESTING
                await self._emit_event("testing_started", {"task_id": task_id})

                test_result = await self.test_runner.run_tests(
                    timeout=self.config.test_timeout,
                )

                await self._emit_event("testing_completed", {
                    "task_id": task_id,
                    "success": test_result.success,
                    "passed": test_result.passed,
                    "failed": test_result.failed,
                })

                if not test_result.success:
                    self._current_state = WorkflowState.FAILED
                    return WorkflowResult(
                        task_id=task_id,
                        success=False,
                        state=self._current_state,
                        branch=branch,
                        test_result=test_result,
                        error=f"Tests failed: {test_result.failed} failures",
                    )

            # Ensure all changes are committed
            final_commit = await self.commit_changes(
                "Final changes before PR",
            )
            if final_commit:
                commits.append(final_commit)

            # Push to remote
            self._current_state = WorkflowState.PUSHING
            await self._emit_event("pushing", {"task_id": task_id, "branch": branch})

            try:
                await self.git.push(set_upstream=True)
            except Exception as e:
                # May need to create repo first
                if "remote" in str(e).lower():
                    logger.info("Creating GitHub repository")
                    await self.github.create_repo(
                        name=self.project_dir.name,
                        push_source=True,
                    )

            # Create PR if configured
            if self.config.auto_create_pr:
                self._current_state = WorkflowState.PR_CREATING
                await self._emit_event("pr_creating", {"task_id": task_id})

                # Generate PR title/body if not provided
                if not pr_title:
                    pr_title = f"[{task_id}] {branch.replace(f'{self.config.branch_prefix}/', '').replace('-', ' ').title()}"

                if not pr_body:
                    recent_commits = await self.git.get_recent_commits(10)
                    commit_list = "\n".join(
                        f"- {c.message}" for c in recent_commits
                        if c.message != "Final changes before PR"
                    )
                    pr_body = f"""## Summary

This PR was automatically created by Cogent Agent.

## Changes

{commit_list}

## Test Results

{"✅ All tests passed" if test_result and test_result.success else "⚠️ Tests not run or failed"}

---
🤖 Generated by Cogent Agent
"""

                pull_request = await self.github.create_pull_request(
                    title=pr_title,
                    body=pr_body,
                    base=self.config.default_base_branch,
                    draft=self.config.pr_draft,
                    labels=self.config.default_labels or None,
                    reviewers=self.config.default_reviewers or None,
                )

                await self._emit_event("pr_created", {
                    "task_id": task_id,
                    "pr_number": pull_request.number,
                    "pr_url": pull_request.url,
                })

                # Enable auto-merge if configured
                if self.config.auto_merge:
                    self._current_state = WorkflowState.WAITING_CHECKS
                    await self.github.merge_pull_request(
                        number=pull_request.number,
                        method="squash",
                        delete_branch=self.config.delete_branch_after_merge,
                        auto=True,
                    )

                    await self._emit_event("auto_merge_enabled", {
                        "task_id": task_id,
                        "pr_number": pull_request.number,
                    })

            self._current_state = WorkflowState.COMPLETED
            self._current_task_id = None

            await self._emit_event("workflow_completed", {
                "task_id": task_id,
                "pr_url": pull_request.url if pull_request else None,
            })

            return WorkflowResult(
                task_id=task_id,
                success=True,
                state=self._current_state,
                branch=branch,
                commits=commits,
                test_result=test_result,
                pull_request=pull_request,
            )

        except Exception as e:
            self._current_state = WorkflowState.FAILED
            logger.exception("Failed to complete task")

            await self._emit_event("workflow_failed", {
                "task_id": task_id,
                "error": str(e),
            })

            return WorkflowResult(
                task_id=task_id,
                success=False,
                state=self._current_state,
                error=str(e),
            )

    async def _ensure_github_repo(self) -> None:
        """
        Ensure GitHub repository exists and remote is configured.

        Creates GitHub repo if it doesn't exist and sets up origin remote.
        """
        # Check if we already have a remote
        status = await self.git.get_status()
        if status.has_remote:
            logger.debug("Remote already configured", remote_url=status.remote_url)
            return

        # Check if GitHub repo exists
        repo_info = await self.github.get_repo_info()

        if repo_info:
            # Repo exists on GitHub, just need to add remote
            logger.info("GitHub repo exists, adding remote", url=repo_info.url)
            await self.git.add_remote("origin", f"{repo_info.url}.git")
        else:
            # Create new GitHub repository
            logger.info("Creating new GitHub repository", name=self.project_dir.name)

            # Make an initial commit if repo is empty
            git_status = await self.git.get_status()
            if not git_status.is_clean or not await self._has_commits():
                await self.git.stage_files()
                await self.git.commit(
                    "Initial commit\n\n🤖 Generated by Cogent Agent",
                    allow_empty=True,
                )

            # Create repo and push
            await self.github.create_repo(
                name=self.project_dir.name,
                description="Project managed by Cogent Agent",
                private=True,
                push_source=True,
            )

            await self._emit_event("github_repo_created", {
                "name": self.project_dir.name,
            })

    async def _has_commits(self) -> bool:
        """Check if the repository has any commits."""
        try:
            commits = await self.git.get_recent_commits(1)
            return len(commits) > 0
        except Exception:
            return False

    def _generate_branch_name(self, description: str, task_id: str) -> str:
        """Generate a branch name from task description."""
        import re

        # Sanitize description
        slug = description.lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"\s+", "-", slug)
        slug = slug[:30].strip("-")

        return f"{self.config.branch_prefix}/{task_id}-{slug}"

    def _start_auto_commit(self) -> None:
        """Start auto-commit background task."""
        if self._auto_commit_task:
            return

        async def auto_commit_loop():
            while True:
                await asyncio.sleep(self.config.auto_commit_interval)
                try:
                    status = await self.git.get_status()
                    if not status.is_clean:
                        await self.commit_changes("Auto-commit: work in progress")
                except Exception as e:
                    logger.error("Auto-commit failed", error=str(e))

        self._auto_commit_task = asyncio.create_task(auto_commit_loop())

    def _stop_auto_commit(self) -> None:
        """Stop auto-commit background task."""
        if self._auto_commit_task:
            self._auto_commit_task.cancel()
            self._auto_commit_task = None

    async def get_status(self) -> dict[str, Any]:
        """Get current workflow status."""
        git_status = await self.git.get_status()

        return {
            "state": self._current_state.value,
            "task_id": self._current_task_id,
            "branch": git_status.branch,
            "is_clean": git_status.is_clean,
            "modified_files": len(git_status.modified_files),
            "staged_files": len(git_status.staged_files),
            "last_commit": self._last_commit_time.isoformat() if self._last_commit_time else None,
        }

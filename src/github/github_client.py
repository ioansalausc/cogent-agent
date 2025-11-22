"""
GitHub integration for Cogent Agent.

Handles:
- Repository creation and management
- Pull request automation
- CI/CD status monitoring
- Auto-merge on passing checks
"""

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


class PRState(Enum):
    """Pull request state."""

    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class CheckStatus(Enum):
    """CI check status."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass
class PullRequest:
    """Pull request information."""

    number: int
    title: str
    body: str
    state: PRState
    branch: str
    base_branch: str
    url: str
    mergeable: bool = True
    checks_status: CheckStatus = CheckStatus.PENDING
    checks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Repository:
    """Repository information."""

    owner: str
    name: str
    full_name: str
    url: str
    default_branch: str
    is_private: bool = False


class GitHubClient:
    """
    GitHub operations using the gh CLI.

    Provides high-level operations for:
    - Repository management
    - Pull request workflows
    - CI/CD integration
    """

    def __init__(self, project_dir: Path):
        """
        Initialize GitHub client.

        Args:
            project_dir: Path to the project directory.
        """
        self.project_dir = Path(project_dir)

    async def _run_gh(
        self,
        *args: str,
        check: bool = True,
        capture_json: bool = False,
    ) -> tuple[int, str, str]:
        """
        Run a gh CLI command.

        Args:
            *args: gh command arguments.
            check: Raise exception on non-zero exit.
            capture_json: Parse output as JSON.

        Returns:
            Tuple of (return_code, stdout, stderr).
        """
        cmd = ["gh"] + list(args)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.project_dir),
        )

        stdout, stderr = await process.communicate()
        stdout_str = stdout.decode().strip() if stdout else ""
        stderr_str = stderr.decode().strip() if stderr else ""

        if check and process.returncode != 0:
            raise GitHubError(f"gh command failed: {' '.join(args)}\n{stderr_str}")

        return process.returncode, stdout_str, stderr_str

    async def is_authenticated(self) -> bool:
        """Check if gh CLI is authenticated."""
        code, _, _ = await self._run_gh("auth", "status", check=False)
        return code == 0

    async def get_repo_info(self) -> Optional[Repository]:
        """Get repository information."""
        try:
            _, output, _ = await self._run_gh(
                "repo", "view", "--json",
                "owner,name,nameWithOwner,url,defaultBranchRef,isPrivate",
            )
            data = json.loads(output)

            return Repository(
                owner=data["owner"]["login"],
                name=data["name"],
                full_name=data["nameWithOwner"],
                url=data["url"],
                default_branch=data["defaultBranchRef"]["name"],
                is_private=data["isPrivate"],
            )
        except Exception as e:
            logger.warning("Failed to get repo info", error=str(e))
            return None

    async def create_repo(
        self,
        name: str,
        description: str = "",
        private: bool = True,
        push_source: bool = True,
    ) -> Optional[Repository]:
        """
        Create a new GitHub repository.

        Args:
            name: Repository name.
            description: Repository description.
            private: Create as private repo.
            push_source: Push existing source code.

        Returns:
            Repository info if created.
        """
        args = ["repo", "create", name]

        if description:
            args.extend(["--description", description])

        if private:
            args.append("--private")
        else:
            args.append("--public")

        if push_source:
            args.append("--source=.")
            args.append("--push")

        await self._run_gh(*args)
        logger.info("Created GitHub repository", name=name)

        return await self.get_repo_info()

    async def create_pull_request(
        self,
        title: str,
        body: str,
        base: Optional[str] = None,
        head: Optional[str] = None,
        draft: bool = False,
        labels: Optional[list[str]] = None,
        reviewers: Optional[list[str]] = None,
    ) -> PullRequest:
        """
        Create a pull request.

        Args:
            title: PR title.
            body: PR body/description.
            base: Base branch (default: repo default).
            head: Head branch (default: current).
            draft: Create as draft PR.
            labels: Labels to add.
            reviewers: Reviewers to request.

        Returns:
            PullRequest info.
        """
        args = ["pr", "create", "--title", title, "--body", body]

        if base:
            args.extend(["--base", base])
        if head:
            args.extend(["--head", head])
        if draft:
            args.append("--draft")
        if labels:
            args.extend(["--label", ",".join(labels)])
        if reviewers:
            args.extend(["--reviewer", ",".join(reviewers)])

        _, output, _ = await self._run_gh(*args)

        # Extract PR URL from output
        pr_url = output.strip()

        # Get PR details
        return await self.get_pull_request_by_url(pr_url)

    async def get_pull_request(self, number: int) -> PullRequest:
        """Get pull request by number."""
        _, output, _ = await self._run_gh(
            "pr", "view", str(number), "--json",
            "number,title,body,state,headRefName,baseRefName,url,mergeable,statusCheckRollup",
        )
        data = json.loads(output)
        return self._parse_pr_data(data)

    async def get_pull_request_by_url(self, url: str) -> PullRequest:
        """Get pull request by URL."""
        _, output, _ = await self._run_gh(
            "pr", "view", url, "--json",
            "number,title,body,state,headRefName,baseRefName,url,mergeable,statusCheckRollup",
        )
        data = json.loads(output)
        return self._parse_pr_data(data)

    def _parse_pr_data(self, data: dict) -> PullRequest:
        """Parse PR JSON data into PullRequest object."""
        # Parse check status
        checks_status = CheckStatus.PENDING
        checks = []

        status_rollup = data.get("statusCheckRollup", [])
        if status_rollup:
            all_success = True
            any_failure = False

            for check in status_rollup:
                check_info = {
                    "name": check.get("name", check.get("context", "unknown")),
                    "status": check.get("status", check.get("state", "unknown")),
                    "conclusion": check.get("conclusion"),
                }
                checks.append(check_info)

                conclusion = check.get("conclusion", "").upper()
                status = check.get("status", check.get("state", "")).upper()

                if conclusion == "FAILURE" or status == "FAILURE":
                    any_failure = True
                    all_success = False
                elif conclusion not in ("SUCCESS", "SKIPPED") and status not in ("SUCCESS", "COMPLETED"):
                    all_success = False

            if any_failure:
                checks_status = CheckStatus.FAILURE
            elif all_success and checks:
                checks_status = CheckStatus.SUCCESS

        return PullRequest(
            number=data["number"],
            title=data["title"],
            body=data.get("body", ""),
            state=PRState(data["state"].lower()),
            branch=data["headRefName"],
            base_branch=data["baseRefName"],
            url=data["url"],
            mergeable=data.get("mergeable", "UNKNOWN") == "MERGEABLE",
            checks_status=checks_status,
            checks=checks,
        )

    async def merge_pull_request(
        self,
        number: int,
        method: str = "squash",
        delete_branch: bool = True,
        auto: bool = False,
    ) -> bool:
        """
        Merge a pull request.

        Args:
            number: PR number.
            method: Merge method (merge, squash, rebase).
            delete_branch: Delete branch after merge.
            auto: Enable auto-merge when checks pass.

        Returns:
            True if merged or auto-merge enabled.
        """
        args = ["pr", "merge", str(number), f"--{method}"]

        if delete_branch:
            args.append("--delete-branch")

        if auto:
            args.append("--auto")

        await self._run_gh(*args)

        if auto:
            logger.info("Auto-merge enabled for PR", number=number)
        else:
            logger.info("Merged PR", number=number, method=method)

        return True

    async def wait_for_checks(
        self,
        number: int,
        timeout: int = 600,
        poll_interval: int = 30,
    ) -> CheckStatus:
        """
        Wait for CI checks to complete.

        Args:
            number: PR number.
            timeout: Maximum wait time in seconds.
            poll_interval: Time between status checks.

        Returns:
            Final check status.
        """
        elapsed = 0

        while elapsed < timeout:
            pr = await self.get_pull_request(number)

            if pr.checks_status in (CheckStatus.SUCCESS, CheckStatus.FAILURE):
                return pr.checks_status

            logger.debug(
                "Waiting for checks",
                pr=number,
                status=pr.checks_status.value,
                elapsed=elapsed,
            )

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        logger.warning("Timeout waiting for checks", pr=number)
        return CheckStatus.PENDING

    async def close_pull_request(self, number: int, comment: Optional[str] = None) -> bool:
        """Close a pull request without merging."""
        if comment:
            await self._run_gh("pr", "comment", str(number), "--body", comment)

        await self._run_gh("pr", "close", str(number))
        logger.info("Closed PR", number=number)
        return True

    async def add_pr_comment(self, number: int, body: str) -> bool:
        """Add a comment to a pull request."""
        await self._run_gh("pr", "comment", str(number), "--body", body)
        return True

    async def list_open_prs(self) -> list[PullRequest]:
        """List open pull requests."""
        _, output, _ = await self._run_gh(
            "pr", "list", "--json",
            "number,title,body,state,headRefName,baseRefName,url",
        )
        data = json.loads(output)

        return [
            PullRequest(
                number=pr["number"],
                title=pr["title"],
                body=pr.get("body", ""),
                state=PRState(pr["state"].lower()),
                branch=pr["headRefName"],
                base_branch=pr["baseRefName"],
                url=pr["url"],
            )
            for pr in data
        ]


class GitHubError(Exception):
    """GitHub operation error."""

    pass

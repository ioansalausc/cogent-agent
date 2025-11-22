"""
In-container test execution for Cogent Agent.

Automatically detects test frameworks and runs tests.
"""

import asyncio
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class TestFramework(Enum):
    """Supported test frameworks."""

    PYTEST = "pytest"
    JEST = "jest"
    MOCHA = "mocha"
    GO_TEST = "go_test"
    CARGO_TEST = "cargo_test"
    RSPEC = "rspec"
    UNKNOWN = "unknown"


@dataclass
class TestResult:
    """Test execution result."""

    framework: TestFramework
    success: bool
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duration: float = 0.0
    output: str = ""
    error: Optional[str] = None


class TestRunner:
    """
    Detects and runs tests in various frameworks.

    Supports automatic detection of:
    - Python (pytest, unittest)
    - JavaScript/TypeScript (jest, mocha)
    - Go (go test)
    - Rust (cargo test)
    - Ruby (rspec)
    """

    def __init__(self, project_dir: Path):
        """
        Initialize test runner.

        Args:
            project_dir: Project directory to run tests in.
        """
        self.project_dir = Path(project_dir)

    async def detect_framework(self) -> TestFramework:
        """
        Detect the test framework used in the project.

        Returns:
            Detected TestFramework.
        """
        # Check for Python (pytest)
        if (self.project_dir / "pytest.ini").exists() or \
           (self.project_dir / "pyproject.toml").exists() or \
           (self.project_dir / "setup.py").exists():
            if await self._check_command("pytest", "--version"):
                return TestFramework.PYTEST

        # Check for JavaScript/TypeScript (jest)
        if (self.project_dir / "package.json").exists():
            package_json = self.project_dir / "package.json"
            content = package_json.read_text()
            if "jest" in content:
                return TestFramework.JEST
            if "mocha" in content:
                return TestFramework.MOCHA

        # Check for Go
        if list(self.project_dir.glob("*.go")) or (self.project_dir / "go.mod").exists():
            return TestFramework.GO_TEST

        # Check for Rust
        if (self.project_dir / "Cargo.toml").exists():
            return TestFramework.CARGO_TEST

        # Check for Ruby (rspec)
        if (self.project_dir / "Gemfile").exists():
            gemfile = (self.project_dir / "Gemfile").read_text()
            if "rspec" in gemfile:
                return TestFramework.RSPEC

        return TestFramework.UNKNOWN

    async def _check_command(self, *args: str) -> bool:
        """Check if a command is available."""
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(self.project_dir),
            )
            await process.wait()
            return process.returncode == 0
        except Exception:
            return False

    async def run_tests(
        self,
        framework: Optional[TestFramework] = None,
        timeout: int = 300,
        coverage: bool = False,
        specific_tests: Optional[list[str]] = None,
    ) -> TestResult:
        """
        Run tests using the detected or specified framework.

        Args:
            framework: Test framework to use (auto-detected if None).
            timeout: Test timeout in seconds.
            coverage: Generate coverage report.
            specific_tests: Specific test files/patterns to run.

        Returns:
            TestResult with execution details.
        """
        if framework is None:
            framework = await self.detect_framework()

        if framework == TestFramework.UNKNOWN:
            return TestResult(
                framework=framework,
                success=False,
                error="Could not detect test framework",
            )

        logger.info("Running tests", framework=framework.value)

        # Build test command based on framework
        cmd, env = self._build_test_command(framework, coverage, specific_tests)

        # Run tests
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(self.project_dir),
                env={**os.environ, **env},
            )

            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
                output = stdout.decode() if stdout else ""
            except asyncio.TimeoutError:
                process.kill()
                return TestResult(
                    framework=framework,
                    success=False,
                    error=f"Tests timed out after {timeout}s",
                )

            # Parse results
            result = self._parse_results(framework, output, process.returncode)
            return result

        except Exception as e:
            logger.exception("Test execution failed")
            return TestResult(
                framework=framework,
                success=False,
                error=str(e),
            )

    def _build_test_command(
        self,
        framework: TestFramework,
        coverage: bool,
        specific_tests: Optional[list[str]],
    ) -> tuple[list[str], dict[str, str]]:
        """Build the test command based on framework."""
        env: dict[str, str] = {}

        if framework == TestFramework.PYTEST:
            cmd = ["pytest", "-v", "--tb=short"]
            if coverage:
                cmd.extend(["--cov", "--cov-report=term-missing"])
            if specific_tests:
                cmd.extend(specific_tests)

        elif framework == TestFramework.JEST:
            cmd = ["npx", "jest", "--verbose"]
            if coverage:
                cmd.append("--coverage")
            if specific_tests:
                cmd.extend(specific_tests)
            env["CI"] = "true"

        elif framework == TestFramework.MOCHA:
            cmd = ["npx", "mocha", "--reporter", "spec"]
            if specific_tests:
                cmd.extend(specific_tests)

        elif framework == TestFramework.GO_TEST:
            cmd = ["go", "test", "-v"]
            if coverage:
                cmd.append("-cover")
            if specific_tests:
                cmd.extend(specific_tests)
            else:
                cmd.append("./...")

        elif framework == TestFramework.CARGO_TEST:
            cmd = ["cargo", "test", "--", "--nocapture"]
            if specific_tests:
                cmd.extend(specific_tests)

        elif framework == TestFramework.RSPEC:
            cmd = ["bundle", "exec", "rspec", "--format", "documentation"]
            if specific_tests:
                cmd.extend(specific_tests)

        else:
            cmd = ["echo", "Unknown test framework"]

        return cmd, env

    def _parse_results(
        self,
        framework: TestFramework,
        output: str,
        return_code: int,
    ) -> TestResult:
        """Parse test output to extract results."""
        result = TestResult(
            framework=framework,
            success=return_code == 0,
            output=output,
        )

        # Try to extract test counts from output
        if framework == TestFramework.PYTEST:
            # Look for pytest summary line like "5 passed, 2 failed, 1 skipped"
            import re

            match = re.search(
                r"(\d+) passed(?:.*?(\d+) failed)?(?:.*?(\d+) skipped)?",
                output,
            )
            if match:
                result.passed = int(match.group(1))
                result.failed = int(match.group(2)) if match.group(2) else 0
                result.skipped = int(match.group(3)) if match.group(3) else 0
                result.total = result.passed + result.failed + result.skipped

        elif framework == TestFramework.JEST:
            import re

            # Jest summary: Tests: X passed, Y failed, Z total
            match = re.search(
                r"Tests:\s+(?:(\d+) passed)?(?:,?\s*(\d+) failed)?(?:,?\s*(\d+) total)?",
                output,
            )
            if match:
                result.passed = int(match.group(1)) if match.group(1) else 0
                result.failed = int(match.group(2)) if match.group(2) else 0
                result.total = int(match.group(3)) if match.group(3) else result.passed + result.failed

        elif framework == TestFramework.GO_TEST:
            import re

            # Count PASS/FAIL lines
            result.passed = len(re.findall(r"^--- PASS:", output, re.MULTILINE))
            result.failed = len(re.findall(r"^--- FAIL:", output, re.MULTILINE))
            result.total = result.passed + result.failed

        logger.info(
            "Test results",
            framework=framework.value,
            success=result.success,
            passed=result.passed,
            failed=result.failed,
            total=result.total,
        )

        return result

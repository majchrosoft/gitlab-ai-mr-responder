from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestResult:
    success: bool
    return_code: int
    command: str
    stdout: str
    stderr: str


class TestRunner:
    def __init__(
        self,
        command: str,
    ) -> None:
        self.command = command

    def run(
        self,
        working_directory: Path,
        project_directory: Path,
        merge_request_directory: Path,
        merge_request_iid: int,
        dry_run: bool = False,
    ) -> TestResult:
        if not working_directory.exists():
            raise RuntimeError(
                "Test working directory does not exist: "
                f"{working_directory}"
            )

        if not working_directory.is_dir():
            raise RuntimeError(
                "Test working directory is not a directory: "
                f"{working_directory}"
            )

        command = self._build_command(
            project_directory=project_directory,
            merge_request_directory=merge_request_directory,
            merge_request_iid=merge_request_iid,
        )

        if dry_run:
            print()
            print("=" * 80)
            print("DRY RUN - Tests would be executed")
            print()
            print("Command:")
            print(command)
            print()
            print("Working directory:")
            print(working_directory)
            print("=" * 80)
            print()

            return TestResult(
                success=True,
                return_code=0,
                command=command,
                stdout="",
                stderr="",
            )

        print()
        print(
            "      Running tests: "
            f"{command}"
        )

        try:
            process = subprocess.run(
                command,
                cwd=working_directory,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            raise RuntimeError(
                "Unable to start test command: "
                f"{command}: {exc}"
            ) from exc

        result = TestResult(
            success=process.returncode == 0,
            return_code=process.returncode,
            command=command,
            stdout=process.stdout,
            stderr=process.stderr,
        )

        print(
            "      Test return code: "
            f"{result.return_code}"
        )

        if result.stdout.strip():
            print()
            print("      Test output:")
            print(result.stdout.strip())

        if result.stderr.strip():
            print()
            print("      Test errors:")
            print(result.stderr.strip())

        if result.success:
            print()
            print("      → Tests PASSED")
        else:
            print()
            print("      → Tests FAILED")

        return result

    def _build_command(
        self,
        project_directory: Path,
        merge_request_directory: Path,
        merge_request_iid: int,
    ) -> str:
        variables = {
            "project_dir": str(project_directory),
            "mr_dir": str(merge_request_directory),
            "mr_iid": str(merge_request_iid),
        }

        try:
            return self.command.format(**variables)
        except KeyError as exc:
            variable = exc.args[0]

            raise RuntimeError(
                "Unknown TEST_COMMAND variable: "
                f"{{{variable}}}. "
                "Available variables: "
                "{project_dir}, {mr_dir}, {mr_iid}"
            ) from exc
        except ValueError as exc:
            raise RuntimeError(
                "Invalid TEST_COMMAND format: "
                f"{exc}"
            ) from exc

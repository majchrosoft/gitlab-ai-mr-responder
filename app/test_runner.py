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
        base_path: Path,
        after_run_command: str | None = None,
    ) -> None:
        self.command = command
        self.base_path = base_path
        self.after_run_command = after_run_command

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
            base_path=self.base_path,
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

    def run_after_run_command(
        self,
        working_directory: Path,
        project_directory: Path,
        merge_request_directory: Path,
        merge_request_iid: int,
        dry_run: bool,
    ) -> None:
        if not self.after_run_command:
            return

        command = self._build_command(
            project_directory=project_directory,
            merge_request_directory=merge_request_directory,
            merge_request_iid=merge_request_iid,
            base_path=self.base_path,
            command=self.after_run_command,
        )

        if dry_run:
            print()
            print("DRY RUN - After-run command would be executed")
            print(command)
            print()
            return

        print()
        print(
            "      Running after-run command: "
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
            print(
                "      → After-run command failed "
                f"to start: {exc}"
            )
            return

        if process.returncode != 0:
            print()
            print(
                "      → After-run command failed "
                f"with code {process.returncode}"
            )

            if process.stdout.strip():
                print(process.stdout.strip())

            if process.stderr.strip():
                print(process.stderr.strip())

            return

        print(
            "      → After-run command completed"
        )

    def _build_command(
        self,
        project_directory: Path,
        merge_request_directory: Path,
        merge_request_iid: int,
        base_path: Path,
        command: str | None = None,
    ) -> str:
        variables = {
            "project_dir": str(project_directory),
            "project_name": project_directory.name,
            "mr_dir": str(merge_request_directory),
            "mr_iid": str(merge_request_iid),
            "base_path": str(base_path),
        }

        command = command if command is not None else self.command
        for name, value in variables.items():
            command = command.replace("{" + name + "}", value)

        return command

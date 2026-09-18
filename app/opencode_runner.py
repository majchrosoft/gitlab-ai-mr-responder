from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OpenCodeResult:
    return_code: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.return_code == 0


class OpenCodeRunner:
    def __init__(
        self,
        command: str,
    ) -> None:
        self.command = command

    def run(
        self,
        prompt: str,
        working_directory: Path,
        model: str | None = None,
        agent: str | None = None,
        auto_approve: bool = False,
        dry_run: bool = False,
    ) -> OpenCodeResult:
        if not working_directory.exists():
            raise RuntimeError(
                "OpenCode working directory does not exist: "
                f"{working_directory}"
            )

        if not working_directory.is_dir():
            raise RuntimeError(
                "OpenCode working directory is not a directory: "
                f"{working_directory}"
            )

        command = [
            self.command,
            "run",
            "--dir",
            str(working_directory),
            "--format",
            "json",
        ]

        if model:
            command.extend(
                [
                    "--model",
                    model,
                ]
            )

        if agent:
            command.extend(
                [
                    "--agent",
                    agent,
                ]
            )

        if auto_approve:
            command.append("--auto")

        command.append(prompt)

        if dry_run:
            print()
            print("=" * 80)
            print("DRY RUN - OpenCode would be executed")
            print()
            print(
                "Command:"
            )
            print(
                " ".join(
                    self._quote_argument(argument)
                    for argument in command
                )
            )
            print()
            print("Working directory:")
            print(working_directory)
            print()
            print("Prompt:")
            print(prompt)
            print("=" * 80)
            print()

            return OpenCodeResult(
                return_code=0,
                stdout="",
                stderr="",
            )

        print()
        print(
            "      Running OpenCode..."
        )

        try:
            process = subprocess.run(
                command,
                cwd=working_directory,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            raise RuntimeError(
                "Unable to start OpenCode: "
                f"{exc}"
            ) from exc

        return OpenCodeResult(
            return_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )

    @staticmethod
    def _quote_argument(
        argument: str,
    ) -> str:
        if not argument:
            return "''"

        if any(
            character.isspace()
            for character in argument
        ):
            escaped = argument.replace(
                "'",
                "'\\''",
            )

            return f"'{escaped}'"

        return argument

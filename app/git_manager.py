from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitChanges:
    has_changes: bool
    changed_files: list[str]
    diff_stat: str


@dataclass(frozen=True)
class GitCommitResult:
    commit_sha: str
    message: str


@dataclass(frozen=True)
class GitPushResult:
    commit_sha: str
    remote: str
    branch: str


class GitManager:
    def __init__(
        self,
        repository_path: Path,
    ) -> None:
        self.repository_path = repository_path

    def get_changes(self) -> GitChanges:
        result = self._run(
            [
                "git",
                "status",
                "--porcelain",
            ]
        )

        changed_files = []

        for line in result.stdout.splitlines():
            line = line.rstrip()

            if not line:
                continue

            if len(line) >= 3:
                changed_files.append(line[3:])

        diff_result = self._run(
            [
                "git",
                "diff",
                "--stat",
            ]
        )

        return GitChanges(
            has_changes=bool(changed_files),
            changed_files=changed_files,
            diff_stat=diff_result.stdout.strip(),
        )

    def get_current_sha(self) -> str:
        result = self._run(
            [
                "git",
                "rev-parse",
                "HEAD",
            ]
        )

        return result.stdout.strip()

    def verify_clean_before_agent(self) -> None:
        changes = self.get_changes()

        if changes.has_changes:
            files = "\n".join(
                f"  - {path}"
                for path in changes.changed_files
            )

            raise RuntimeError(
                "Repository is not clean before "
                "starting OpenCode.\n"
                f"Changed files:\n{files}"
            )

    def verify_agent_commit_unchanged(
        self,
        base_sha: str,
    ) -> None:
        current_sha = self.get_current_sha()

        if current_sha != base_sha:
            raise RuntimeError(
                "OpenCode changed the Git commit.\n"
                f"Expected: {base_sha}\n"
                f"Current:  {current_sha}"
            )

    def fetch_origin(self) -> None:
        self._run(
            [
                "git",
                "fetch",
                "origin",
                "--prune",
            ]
        )

    def get_remote_branch_sha(
        self,
        branch: str,
    ) -> str | None:
        result = self._run(
            [
                "git",
                "rev-parse",
                f"refs/remotes/origin/{branch}",
            ],
            check=False,
        )

        if result.returncode != 0:
            return None

        return result.stdout.strip()

    def verify_remote_branch(
        self,
        branch: str,
        expected_sha: str,
    ) -> None:
        self.fetch_origin()

        remote_sha = self.get_remote_branch_sha(
            branch
        )

        if remote_sha is None:
            raise RuntimeError(
                "Remote branch does not exist: "
                f"origin/{branch}"
            )

        print()
        print(
            "      Remote branch SHA: "
            f"{remote_sha}"
        )

        print(
            "      Previous SHA: "
            f"{expected_sha}"
        )

    def create_commit(
        self,
        message: str,
    ) -> GitCommitResult:
        changes = self.get_changes()

        if not changes.has_changes:
            raise RuntimeError(
                "Cannot create commit: "
                "working tree has no changes."
            )

        self._run(
            [
                "git",
                "add",
                "--all",
            ]
        )

        self._run(
            [
                "git",
                "commit",
                "-m",
                message,
            ]
        )

        commit_sha = self.get_current_sha()

        return GitCommitResult(
            commit_sha=commit_sha,
            message=message,
        )

    def push_to_branch(
        self,
        branch: str,
    ) -> GitPushResult:
        if not branch.strip():
            raise RuntimeError(
                "Cannot push: branch name is empty."
            )

        commit_sha = self.get_current_sha()

        print()
        print(
            "      Force pushing commit "
            f"{commit_sha}"
        )

        self._run(
            [
                "git",
                "push",
                "--force",
                "origin",
                f"HEAD:{branch}",
            ]
        )

        return GitPushResult(
            commit_sha=commit_sha,
            remote="origin",
            branch=branch,
        )

    def get_diff(self) -> str:
        result = self._run(
            [
                "git",
                "diff",
            ]
        )

        return result.stdout

    def _run(
        self,
        command: list[str],
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                cwd=self.repository_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            raise RuntimeError(
                "Unable to execute command: "
                f"{' '.join(command)}"
            ) from exc

        if check and result.returncode != 0:
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            details = "\n".join(
                part
                for part in [
                    stdout,
                    stderr,
                ]
                if part
            )

            raise RuntimeError(
                "Git command failed:\n"
                f"  {' '.join(command)}\n"
                f"  cwd: {self.repository_path}\n"
                f"{details}"
            )

        return result
